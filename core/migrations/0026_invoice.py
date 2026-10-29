import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_merge_store_branches"),
    ]

    operations = [
        migrations.CreateModel(
            name="Invoice",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("session_key", models.CharField(db_index=True, max_length=64)),
                ("request_id", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("amount_sats", models.PositiveBigIntegerField()),
                (
                    "payment_hash",
                    models.CharField(max_length=128, unique=True),
                ),
                ("bolt11", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Aguardando pagamento"),
                            ("paid", "Pago"),
                            ("expired", "Expirada"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("session_key", "request_id"),
                        name="core_invoice_unique_session_request",
                    )
                ],
            },
        ),
    ]
