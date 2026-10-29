#!/usr/bin/env bash
# Auxiliar conservador de execução. Não sobrescreve .env, não cria usuários,
# não altera credenciais e não remove dados.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "ERRO: .env ausente. Copie .env.example para .env e preencha as credenciais." >&2
    exit 1
fi

if [[ -x venv/bin/python ]]; then
    PYTHON=venv/bin/python
else
    PYTHON=python3
fi

"$PYTHON" manage.py check
"$PYTHON" manage.py migrate --noinput
"$PYTHON" manage.py collectstatic --noinput

if command -v docker >/dev/null 2>&1; then
    docker compose up -d --build
else
    echo "Docker indisponível. Aplicação preparada; inicie com: $PYTHON manage.py runserver"
fi
