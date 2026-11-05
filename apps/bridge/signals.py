from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.outbox.models import OutboxEvent

from .models import StreamEvent


@receiver(post_save, sender=OutboxEvent)
def record_stream_event(sender, instance, created, **kwargs):
    if created:
        # Same transaction as the hardware journal; no ephemeral pub/sub dependency.
        StreamEvent.objects.create(topic=instance.topic, payload=instance.payload)
