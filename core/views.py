"""Views do terminal POS.

Toda confirmação de pagamento vem do LNbits, nunca do navegador. O frontend
consulta apenas o UUID opaco da cobrança; a sessão prova a propriedade e o
``payment_hash`` permanece no servidor.
"""

import json
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from .models import Invoice
from .services.lnbits import (
    LightningNotConfigured,
    LightningRejected,
    LightningUnavailable,
    create_invoice as create_lnbits_invoice,
    get_payment,
)

logger = logging.getLogger(__name__)


@never_cache
def home(request):
    return render(
        request,
        "pos.html",
        {
            "min_sats": settings.POS_MIN_SATS,
            "max_sats": settings.POS_MAX_SATS,
            "min_sats_display": f"{settings.POS_MIN_SATS:,}".replace(",", "."),
            "max_sats_display": f"{settings.POS_MAX_SATS:,}".replace(",", "."),
            "payments_ready": bool(settings.LNBITS_URL and settings.LNBITS_API_KEY),
        },
    )


def _json(payload, status=200):
    response = JsonResponse(
        payload,
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def _error(message, status, code):
    return _json({"error": message, "code": code}, status=status)


def _serialize_invoice(invoice):
    return {
        "invoice_id": str(invoice.id),
        "amount_sats": invoice.amount_sats,
        "payment_request": invoice.bolt11,
        "status": invoice.status,
        "paid": invoice.is_paid,
        "created_at": invoice.created_at.isoformat(),
        "expires_at": invoice.expires_at.isoformat() if invoice.expires_at else None,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
    }


def _session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _read_json(request):
    if request.content_type != "application/json":
        return None
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _parse_request_id(value):
    if value is None:
        return uuid.uuid4()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("request_id inválido")
    return uuid.UUID(value.strip())


def _amount_error(amount):
    if isinstance(amount, bool) or not isinstance(amount, int):
        return "Informe um valor inteiro em satoshis."
    if amount < settings.POS_MIN_SATS or amount > settings.POS_MAX_SATS:
        return (
            f"O valor deve estar entre {settings.POS_MIN_SATS} e "
            f"{settings.POS_MAX_SATS} sats."
        )
    return ""


@require_POST
@csrf_protect
def invoice_create(request):
    data = _read_json(request)
    if data is None:
        return _error("Corpo JSON inválido.", 400, "invalid_json")

    amount_sats = data.get("amount_sats")
    amount_error = _amount_error(amount_sats)
    if amount_error:
        return _error(amount_error, 400, "invalid_amount")

    try:
        request_id = _parse_request_id(data.get("request_id"))
    except (ValueError, TypeError, AttributeError):
        return _error("Identificador de requisição inválido.", 400, "invalid_request_id")

    session_key = _session_key(request)
    existing = Invoice.objects.filter(
        session_key=session_key,
        request_id=request_id,
    ).first()
    if existing:
        if existing.amount_sats != amount_sats:
            return _error(
                "Esta requisição já foi usada com outro valor.",
                409,
                "request_id_conflict",
            )
        return _json(_serialize_invoice(existing), status=200)

    try:
        created = create_lnbits_invoice(
            amount_sats=amount_sats,
            memo=f"browser-POS · {amount_sats} sats",
        )
    except LightningNotConfigured:
        logger.error("Criação de invoice indisponível: LNbits não configurado.")
        return _error(
            "Serviço de pagamentos indisponível.",
            503,
            "payments_not_configured",
        )
    except LightningRejected:
        return _error(
            "O serviço de pagamentos recusou a cobrança.",
            502,
            "payment_rejected",
        )
    except LightningUnavailable:
        return _error(
            "Não foi possível contactar o serviço de pagamentos.",
            503,
            "payment_unavailable",
        )

    now = timezone.now()
    expires_at = created.expires_at or (
        now + timedelta(seconds=settings.POS_INVOICE_TTL_SECONDS)
    )

    try:
        invoice = Invoice.objects.create(
            session_key=session_key,
            request_id=request_id,
            amount_sats=amount_sats,
            payment_hash=created.payment_hash,
            bolt11=created.payment_request,
            status=Invoice.Status.PENDING,
            expires_at=expires_at,
        )
    except IntegrityError:
        # Duas requisições idempotentes concorrentes: devolve a já persistida.
        invoice = Invoice.objects.filter(
            session_key=session_key,
            request_id=request_id,
        ).first()
        if invoice is None:
            logger.error("Falha de integridade ao persistir a cobrança.")
            return _error("Não foi possível registrar a cobrança.", 500, "storage_error")
        return _json(_serialize_invoice(invoice), status=200)
    except Exception as exc:
        logger.error("Falha ao persistir a cobrança (%s).", type(exc).__name__)
        return _error("Não foi possível registrar a cobrança.", 500, "storage_error")

    return _json(_serialize_invoice(invoice), status=201)


def _invoice_for_session(request, invoice_id):
    if not request.session.session_key:
        return None
    return Invoice.objects.filter(
        pk=invoice_id,
        session_key=request.session.session_key,
    ).first()


@require_GET
def invoice_status(request, invoice_id):
    invoice = _invoice_for_session(request, invoice_id)
    if invoice is None:
        return _error("Cobrança não encontrada.", 404, "invoice_not_found")

    # Pagamento confirmado é terminal e permanece confirmado.
    if invoice.is_paid:
        return _json(_serialize_invoice(invoice))

    try:
        snapshot = get_payment(invoice.payment_hash)
    except LightningNotConfigured:
        logger.error("Consulta de pagamento indisponível: LNbits não configurado.")
        return _error(
            "Serviço de pagamentos indisponível.",
            503,
            "payments_not_configured",
        )
    except LightningRejected:
        return _error(
            "Não foi possível consultar a cobrança no serviço de pagamentos.",
            502,
            "payment_query_rejected",
        )
    except LightningUnavailable:
        return _error(
            "Não foi possível contactar o serviço de pagamentos.",
            503,
            "payment_unavailable",
        )

    now = timezone.now()
    new_status = invoice.status
    effective_expiry = snapshot.expires_at or invoice.expires_at

    if snapshot.paid:
        new_status = Invoice.Status.PAID
    elif snapshot.status in {"failed", "expired"}:
        new_status = Invoice.Status.EXPIRED
    elif effective_expiry and now >= effective_expiry:
        new_status = Invoice.Status.EXPIRED

    changed_fields = []

    if invoice.status != new_status:
        invoice.status = new_status
        changed_fields.append("status")
    if new_status == Invoice.Status.PAID and invoice.paid_at is None:
        invoice.paid_at = now
        changed_fields.append("paid_at")
    if snapshot.expires_at and invoice.expires_at != snapshot.expires_at:
        invoice.expires_at = snapshot.expires_at
        changed_fields.append("expires_at")

    invoice.last_checked_at = now
    changed_fields.append("last_checked_at")
    changed_fields.append("updated_at")
    invoice.save(update_fields=changed_fields)

    return _json(_serialize_invoice(invoice))
