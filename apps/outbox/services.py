from collections.abc import Mapping
from typing import Any

from .models import OutboxEvent


def enqueue_event(
    *,
    topic: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Mapping[str, Any],
) -> OutboxEvent:
    return OutboxEvent.objects.create(
        topic=topic,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=dict(payload),
    )
