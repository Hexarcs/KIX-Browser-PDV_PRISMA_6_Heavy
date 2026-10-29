"""Modelos do browser-POS.

A única entidade persistida é a fatura criada pelo terminal. O ``payment_hash``
fica no servidor e nunca é devolvido ao navegador; o frontend recebe apenas o
UUID opaco da cobrança e a invoice BOLT11 necessária para o QR code.
"""

import uuid

from django.db import models


class Invoice(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Aguardando pagamento"
        PAID = "paid", "Pago"
        EXPIRED = "expired", "Expirada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_key = models.CharField(max_length=64, db_index=True)
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)

    amount_sats = models.PositiveBigIntegerField()
    payment_hash = models.CharField(max_length=128, unique=True)
    bolt11 = models.TextField()

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["session_key", "request_id"],
                name="core_invoice_unique_session_request",
            )
        ]

    def __str__(self):
        return f"Cobrança {self.pk} · {self.amount_sats} sats · {self.status}"

    @property
    def is_paid(self):
        return self.status == self.Status.PAID
