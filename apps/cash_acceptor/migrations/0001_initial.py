import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PaymentSession",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=100, unique=True)),
                ("external_reference", models.CharField(blank=True, db_index=True, max_length=128)),
                ("currency", models.CharField(max_length=3)),
                ("requested_amount_minor", models.PositiveBigIntegerField()),
                ("credited_amount_minor", models.PositiveBigIntegerField(default=0)),
                ("consumed_amount_minor", models.PositiveBigIntegerField(default=0)),
                ("credit_balance_minor", models.PositiveBigIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("created", "Creada"),
                            ("accepting", "Aceptando efectivo"),
                            ("completed", "Completada"),
                            ("cancelled", "Cancelada"),
                            ("interrupted", "Interrumpida"),
                        ],
                        db_index=True,
                        default="created",
                        max_length=20,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CashCredit",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("device_event_id", models.CharField(max_length=100, unique=True)),
                ("channel", models.PositiveSmallIntegerField()),
                ("currency", models.CharField(max_length=3)),
                ("denomination_minor", models.PositiveBigIntegerField()),
                ("raw_event", models.JSONField(blank=True, default=dict)),
                ("credited_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cash_credits",
                        to="cash_acceptor.paymentsession",
                    ),
                ),
            ],
            options={"ordering": ["credited_at", "id"]},
        ),
        migrations.AddIndex(
            model_name="cashcredit",
            index=models.Index(fields=["session", "credited_at"], name="cash_credit_session_idx"),
        ),
    ]
