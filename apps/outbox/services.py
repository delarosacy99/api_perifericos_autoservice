from collections.abc import Mapping
from typing import Any
from django.db import transaction
from .realtime import publish_ready

from .models import OutboxEvent


def enqueue_event(
    *,
    topic: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Mapping[str, Any],
) -> OutboxEvent:
    event = OutboxEvent.objects.create(
        topic=topic,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=dict(payload),
    )
    def committed():
        # Wake only after the durable journal commits. Hardware ACKs never
        # depend on a browser or on successful delivery to the central API.
        if topic == "cash.note.credited":
            from apps.station_cash.runtime import wake_sync_worker
            wake_sync_worker()
        if aggregate_type == "door":
            from apps.door_monitor.sync import wake_door_sync
            wake_door_sync()
        group = "station.front" if aggregate_type == "door" else "cash." + aggregate_id
        publish_ready(group)
    transaction.on_commit(committed, robust=True)
    return event
