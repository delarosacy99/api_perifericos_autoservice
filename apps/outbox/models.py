import uuid

from django.db import models


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=100, db_index=True)
    aggregate_type = models.CharField(max_length=50)
    aggregate_id = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    retry_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["occurred_at", "id"]
        indexes = [
            models.Index(fields=["delivered_at", "occurred_at"], name="outbox_pending_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.topic}:{self.aggregate_id}"
