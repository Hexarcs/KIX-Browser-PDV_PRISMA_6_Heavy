# browser-POS

Terminal de pagamentos Bitcoin Lightning, minimalista, para uso no navegador.
O operador digita um valor inteiro em satoshis, gera uma invoice BOLT11, exibe
o QR code e acompanha a confirmação real do pagamento.

A aplicação **não é mais uma loja virtual**: não há catálogo, carrinho,
estoque, checkout comercial, Pix ou cadastro de clientes.

## Stack

- Python 3.11+ / Django 5
- SQLite
- LNbits (API de pagamentos já usada pelo projeto)
- JavaScript puro e CSS local, sem CDN em tempo de execução
- QR code gerado no navegador por `qrcode-generator` vendorizado em
  `core/static/vendor/qrcode.js`

## Fluxo

1. O terminal abre pronto para digitar um inteiro em satoshis.
2. O operador usa o teclado virtual ou o teclado físico e clica em **Gerar
   fatura Lightning**.
3. O navegador faz `POST /api/invoices/` com `amount_sats` e um `request_id`
   idempotente.
4. O backend valida o valor novamente, consulta o LNbits e persiste a cobrança.
   A invoice BOLT11 e um UUID opaco voltam ao navegador; o `payment_hash` fica
   somente no servidor.
5. O navegador renderiza o QR a partir da BOLT11 retornada e faz polling de
   `GET /api/invoices/<uuid>/status/`.
6. O backend consulta `GET {LNBITS_URL}/{payment_hash}`. A interface só exibe
   `PAGO` quando o LNbits confirma `paid: true` (ou `status: success`).
7. Após o pagamento, **Nova cobrança** limpa tudo e prepara o próximo valor.

### Estados da interface

- Pronto para cobrar
- Gerando fatura
- Aguardando pagamento
- Pagamento confirmado (`PAGO`)
- Fatura expirada
- Operação cancelada/abandonada
- Erro de comunicação ou de pagamento

O cancelamento é local: o terminal para de exibir a cobrança, mas **não**
inventa uma invalidação no LNbits. A fatura continua válida até expirar.

## Estrutura principal

```text
core/
├── models.py                 # Invoice (única entidade persistida)
├── views.py                  # home + APIs de criação/consulta
├── services/lnbits.py        # cliente LNbits com validação e timeouts
├── middleware.py             # CSP e cabeçalhos de segurança
├── static/
│   ├── css/pos.css
│   ├── js/pos.js
│   └── vendor/qrcode.js
├── migrations/               # 0025-0027 corrigem/atualizam o schema
└── tests.py                  # testes com LNbits mockado
templates/pos.html            # terminal (única tela)
```

## Configuração

Copie o exemplo e edite:

```bash
cp .env.example .env
chmod 600 .env
```

Variáveis necessárias:

| Variável | Obrigatória | Descrição |
| --- | --- | --- |
| `SECRET_KEY` | sim | Chave longa e aleatória do Django. |
| `LNBITS_URL` | sim | URL completa do endpoint, ex. `https://lnbits.exemplo/api/v1/payments`. |
| `LNBITS_API_KEY` | sim | Chave de invoice do LNbits. Fica apenas no servidor. |
| `DEBUG` | não | `False` por padrão. |
| `ALLOWED_HOSTS` | não | Hosts separados por vírgula. |
| `CSRF_TRUSTED_ORIGINS` | não | Origens HTTPS separadas por vírgula. |
| `LNBITS_TIMEOUT` | não | Timeout HTTP em segundos (padrão 10). |
| `POS_MIN_SATS` | não | Mínimo aceito (padrão 1). |
| `POS_MAX_SATS` | não | Máximo local (padrão 1.000.000). O LNbits pode impor um limite menor. |
| `POS_INVOICE_TTL_SECONDS` | não | Fallback de expiração quando o LNbits não devolve `expiry`. |
| `DATABASE_PATH` | não | Caminho do SQLite. |
| `TRUST_PROXY_HEADERS` | não | `True` atrás de proxy TLS confiável. |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | não | Padrão é `not DEBUG`; use `False` apenas em HTTP local. |
| `SECURE_SSL_REDIRECT` | não | Redireciona HTTP para HTTPS quando servido diretamente. |
| `SECURE_HSTS_SECONDS` | não | HSTS; use apenas com HTTPS estável. |

O backend nunca envia `LNBITS_API_KEY`, `LNBITS_URL` ou `payment_hash` ao
navegador.

## Executar localmente

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # se ainda não existir
# edite .env
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

Abra `http://127.0.0.1:8000/`.

Para testar sem criar cobranças reais, use os testes (o LNbits é mockado):

```bash
python manage.py test -v 2
```

## API interna

### `POST /api/invoices/`

Corpo JSON:

```json
{"amount_sats": 1500, "request_id": "b0e5d9b1-0e2a-4ed8-9d7d-72a1d8d9b1b4"}
```

Resposta `201` (ou `200` em repetição idempotente):

```json
{
  "invoice_id": "UUID",
  "amount_sats": 1500,
  "payment_request": "lnbc...",
  "status": "pending",
  "paid": false,
  "created_at": "...",
  "expires_at": "...",
  "paid_at": null
}
```

O `request_id` evita faturas duplicadas por duplo clique ou retry de rede. Um
mesmo `request_id` com valor diferente devolve `409`.

### `GET /api/invoices/<uuid>/status/`

Consulta o LNbits e devolve o estado atual. A cobrança só é acessível pela
sessão que a criou. O endpoint não aceita `payment_hash` nem revela dados de
outras sessões.

## Integração LNbits preservada

O contrato existente foi mantido:

- `POST {LNBITS_URL}` com `X-Api-Key`, `out=false`, `amount` inteiro,
  `unit="sat"` e `memo`.
- `GET {LNBITS_URL}/{payment_hash}` com `X-Api-Key`.
- Confirmação apenas por `paid: true` ou `status: success` da consulta.
- Erros de rede, timeout, HTTP inválido e corpo inválido são tratados sem
  expor detalhes internos ao navegador ou credenciais nos logs.

## Banco e migrações

O banco existente foi preservado. As migrações antigas da loja continuam no
repositório para manter o histórico e os dados, e foram adicionadas:

- `0025_merge_store_branches` — corrige o grafo que tinha dois nós folha
  conflitantes.
- `0026_invoice` — cria apenas a tabela de cobranças do POS.
- `0027_retire_store_models` — remove os modelos comerciais **do estado do
  Django** usando `SeparateDatabaseAndState` sem operações de banco. As tabelas
  legadas e seus registros continuam no SQLite.

O caminho do banco é resolvido assim: `DATABASE_PATH` se definido; senão
`data/db.sqlite3` se existir; senão `db.sqlite3` na raiz do projeto.

As tabelas `core_produto`, `core_categoria`, `core_configuracaopix` etc. **não
foram apagadas**. Uma remoção física exige backup e decisão separada.

## Segurança

- Validação de inteiro e faixa no frontend e novamente no backend.
- Valores em satoshis como inteiros; sem ponto flutuante no cálculo.
- `payment_hash` e chave do LNbits nunca vão para o navegador.
- Status restrito à sessão que criou a cobrança.
- POST com CSRF; cookies `HttpOnly` e `SameSite=Lax`.
- CSP restritiva, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- Respostas de API com `Cache-Control: no-store`.
- Logs sem corpos, invoices, hashes ou credenciais.
- Rotas da loja, admin, Allauth, Pix, carrinho e contato foram removidas.

> Este terminal cria invoices com a chave do comerciante. Em produção, coloque
> a aplicação atrás de HTTPS e de um controle de acesso de rede (VPN, proxy
> autenticado ou rede interna), pois não há tela de login de operador.

### Antes de produção

- Defina `DEBUG=False`.
- Restrinja `ALLOWED_HOSTS` ao domínio real.
- Preencha `CSRF_TRUSTED_ORIGINS` se houver proxy/domínio diferente.
- Rotacione o `SECRET_KEY` se ele tiver sido gerado como `django-insecure-*`.
- Ative HTTPS, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` e,
  opcionalmente, `SECURE_SSL_REDIRECT`/HSTS.
- Restrinja o acesso ao terminal por VPN, proxy autenticado ou rede interna.
- Não versione `.env`, o banco nem backups.

## Limitações conhecidas

- A confirmação depende da disponibilidade do LNbits; não há webhook.
- O `expiry` real vem do LNbits quando presente; caso contrário, vale o fallback
  configurado para a interface.
- Abandonar uma cobrança não invalida a invoice no nó.
- Não há multiusuário, caixa, sangria, impressão fiscal ou relatórios de venda.
- O deploy automatizado não é executado por este repositório; `deploy.sh` é
  apenas um auxiliar conservador e não sobrescreve o `.env`.
