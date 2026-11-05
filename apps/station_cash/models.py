from django.db import models


class AttentionBinding(models.Model):
    attention_id = models.UUIDField(primary_key=True)
    collection = models.OneToOneField("cash_acceptor.CashCollectionSession", on_delete=models.PROTECT, related_name="central_binding")
    heartbeat_at = models.DateTimeField()
    sealed = models.BooleanField(default=False)
    central_snapshot = models.JSONField(default=dict)
    retry_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=120, blank=True)
