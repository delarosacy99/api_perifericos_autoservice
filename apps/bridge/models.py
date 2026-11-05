import uuid

from django.db import models


class ScreenLease(models.Model):
    # One local station, one controlling screen. Never store the bearer token.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    client_id = models.UUIDField(null=True)
    token_hash = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True)
    event_floor = models.PositiveBigIntegerField(default=0)
    checkout = models.ForeignKey("LocalCheckout", null=True, on_delete=models.SET_NULL)


class LocalCheckout(models.Model):
    id = models.UUIDField(primary_key=True)  # frontend request_key, stable across retries
    remote_id = models.UUIDField(null=True, unique=True)
    access_key = models.UUIDField(default=uuid.uuid4)
    collection = models.OneToOneField(
        "cash_acceptor.CashCollectionSession", null=True, on_delete=models.PROTECT
    )
    snapshot = models.JSONField(default=dict)
    finalize_payload = models.JSONField(null=True)
    finalize_confirmed = models.BooleanField(default=False)
    completed = models.BooleanField(default=False, db_index=True)
    last_error = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CashDelivery(models.Model):
    banknote = models.OneToOneField(
        "cash_acceptor.AcceptedBanknote", primary_key=True, on_delete=models.PROTECT
    )
    delivered_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=120, blank=True)


class PrintJob(models.Model):
    class Status(models.TextChoices):
        READY = "ready"
        DISPATCHING = "dispatching"
        UNCERTAIN = "uncertain"
        FAILED = "failed"
        CONFIRMED = "confirmed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    checkout = models.ForeignKey(LocalCheckout, on_delete=models.PROTECT, related_name="jobs")
    kind = models.CharField(max_length=20)
    document = models.JSONField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.READY)
    attempt_id = models.UUIDField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    evidence = models.JSONField(default=dict)
    resolution_reason = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["checkout", "kind"], name="bridge_one_document_kind")
        ]


class StreamEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    topic = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)
