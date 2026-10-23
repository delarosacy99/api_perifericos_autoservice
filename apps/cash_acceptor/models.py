import uuid

from django.db import models


class CashCollectionSession(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Creada"
        ACCEPTING = "accepting", "Aceptando efectivo"
        STOPPED = "stopped", "Detenida"
        INTERRUPTED = "interrupted", "Interrumpida"

    class StopReason(models.TextChoices):
        REMOTE_COMPLETED = "remote_completed", "El servidor completó la operación"
        USER_CANCELLED = "user_cancelled", "El usuario canceló"
        FRONTEND_DISCONNECTED = "frontend_disconnected", "El frontend se desconectó"
        DEVICE_ERROR = "device_error", "Error del periférico"
        SERVICE_SHUTDOWN = "service_shutdown", "El servicio se está deteniendo"
        OPERATOR_REQUESTED = "operator_requested", "Detenida por un operador"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=100, unique=True)
    remote_session_id = models.CharField(max_length=128, db_index=True)
    currency = models.CharField(max_length=3)
    accepted_amount_minor = models.PositiveBigIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED,
        db_index=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.CharField(max_length=64, choices=StopReason.choices, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.id}:{self.status}:{self.accepted_amount_minor}"


class AcceptedBanknote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        CashCollectionSession,
        on_delete=models.PROTECT,
        related_name="accepted_banknotes",
    )
    device_event_id = models.CharField(max_length=100, unique=True)
    channel = models.PositiveSmallIntegerField()
    currency = models.CharField(max_length=3)
    denomination_minor = models.PositiveBigIntegerField()
    raw_event = models.JSONField(default=dict, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["accepted_at", "id"]
        indexes = [
            models.Index(fields=["session", "accepted_at"], name="accepted_note_session_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.currency}:{self.denomination_minor}:{self.device_event_id}"
