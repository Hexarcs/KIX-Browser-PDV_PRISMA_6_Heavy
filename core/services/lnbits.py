"""Cliente mínimo para a API de pagamentos do LNbits.

Contrato preservado da integração original:

* ``POST {LNBITS_URL}`` com ``out=false``, ``amount`` em sats, ``unit=sat`` e
  ``memo`` cria uma invoice de recebimento.
* ``GET {LNBITS_URL}/{payment_hash}`` consulta o estado da invoice.

A chave de API só é enviada do servidor para o LNbits e nunca aparece em
respostas, templates, logs ou no JavaScript do navegador.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_PAYMENT_HASH_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_BOLT11_RE = re.compile(r"^ln(?:bc|tb|bcrt|tbs)[0-9]", re.IGNORECASE)


class LightningError(Exception):
    """Falha segura e genérica na integração com o serviço Lightning."""


class LightningNotConfigured(LightningError):
    """URL ou chave do LNbits ausente/inválida."""


class LightningRejected(LightningError):
    """O LNbits recusou ou devolveu uma resposta inválida."""


class LightningUnavailable(LightningError):
    """Timeout, rede indisponível ou serviço fora do ar."""


@dataclass(frozen=True)
class CreatedInvoice:
    payment_request: str
    payment_hash: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class PaymentSnapshot:
    paid: bool
    status: str
    expires_at: datetime | None = None


def _configuration():
    base_url = str(getattr(settings, "LNBITS_URL", "") or "").strip().rstrip("/")
    api_key = str(getattr(settings, "LNBITS_API_KEY", "") or "").strip()
    if not base_url or not api_key:
        raise LightningNotConfigured("LNbits não configurado.")

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LightningNotConfigured("URL do LNbits inválida.")

    try:
        timeout = float(getattr(settings, "LNBITS_TIMEOUT", 10))
    except (TypeError, ValueError):
        timeout = 10.0
    return base_url, api_key, max(1.0, timeout)


def _headers(api_key):
    return {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _json_object(response):
    try:
        data = response.json()
    except ValueError as exc:
        raise LightningRejected("Resposta inválida do LNbits.") from exc
    if not isinstance(data, dict):
        raise LightningRejected("Resposta inválida do LNbits.")
    return data


def _parse_expiry(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def create_invoice(*, amount_sats, memo):
    """Cria uma invoice de recebimento com valor exato em satoshis."""
    amount_sats = int(amount_sats)
    if amount_sats <= 0:
        raise LightningRejected("Valor inválido.")

    base_url, api_key, timeout = _configuration()
    payload = {
        "out": False,
        "amount": amount_sats,
        "memo": memo,
        "unit": "sat",
    }

    try:
        response = requests.post(
            base_url,
            json=payload,
            headers=_headers(api_key),
            timeout=timeout,
        )
    except requests.Timeout as exc:
        logger.warning("LNbits create: timeout")
        raise LightningUnavailable("Tempo esgotado no serviço de pagamentos.") from exc
    except requests.RequestException as exc:
        logger.warning("LNbits create: falha de rede (%s)", type(exc).__name__)
        raise LightningUnavailable("Serviço de pagamentos indisponível.") from exc

    if response.status_code not in {200, 201}:
        logger.warning("LNbits create: recusado (HTTP %s)", response.status_code)
        raise LightningRejected("O serviço recusou a criação da cobrança.")

    data = _json_object(response)
    payment_request = data.get("payment_request") or data.get("bolt11")
    payment_hash = data.get("payment_hash")

    if not isinstance(payment_hash, str) or not _PAYMENT_HASH_RE.fullmatch(payment_hash):
        logger.warning("LNbits create: payment_hash inválido")
        raise LightningRejected("Resposta inválida do serviço de pagamentos.")

    if (
        not isinstance(payment_request, str)
        or not _BOLT11_RE.match(payment_request.strip())
        or len(payment_request) > 8000
    ):
        logger.warning("LNbits create: BOLT11 inválida")
        raise LightningRejected("Resposta inválida do serviço de pagamentos.")

    return CreatedInvoice(
        payment_request=payment_request.strip(),
        payment_hash=payment_hash.lower(),
        expires_at=_parse_expiry(data.get("expiry")),
    )


def get_payment(payment_hash):
    """Consulta o estado real da invoice no LNbits."""
    if not isinstance(payment_hash, str) or not _PAYMENT_HASH_RE.fullmatch(payment_hash):
        raise LightningRejected("Identificador de pagamento inválido.")

    base_url, api_key, timeout = _configuration()
    url = f"{base_url}/{quote(payment_hash.lower(), safe='')}"

    try:
        response = requests.get(
            url,
            headers=_headers(api_key),
            timeout=timeout,
        )
    except requests.Timeout as exc:
        logger.warning("LNbits status: timeout")
        raise LightningUnavailable("Tempo esgotado ao consultar o pagamento.") from exc
    except requests.RequestException as exc:
        logger.warning("LNbits status: falha de rede (%s)", type(exc).__name__)
        raise LightningUnavailable("Serviço de pagamentos indisponível.") from exc

    if response.status_code == 404:
        logger.warning("LNbits status: invoice não encontrada")
        raise LightningRejected("Cobrança não encontrada no serviço de pagamentos.")
    if response.status_code != 200:
        logger.warning("LNbits status: falha (HTTP %s)", response.status_code)
        raise LightningUnavailable("Não foi possível consultar o pagamento.")

    data = _json_object(response)
    details = data.get("details") if isinstance(data.get("details"), dict) else {}

    paid = data.get("paid") is True
    remote_status = str(data.get("status") or "").strip().lower()
    if not remote_status and details:
        remote_status = str(details.get("status") or "").strip().lower()
    if remote_status == "success":
        paid = True

    expiry = _parse_expiry(details.get("expiry")) if details else None
    if expiry is None:
        expiry = _parse_expiry(data.get("expiry"))

    return PaymentSnapshot(
        paid=paid,
        status=remote_status or ("paid" if paid else "pending"),
        expires_at=expiry,
    )
