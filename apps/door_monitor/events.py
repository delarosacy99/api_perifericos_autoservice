"""The durable station-wide door stream; independent from cash attentions."""
from .models import DoorMonitorState
from apps.outbox.models import OutboxEvent

DOOR_TOPICS = (
    "door.front.snapshot", "door.front.opened", "door.front.closed",
    "door.front.disconnected", "door.front.recovered", "door.front.unknown",
)


def door_events():
    return OutboxEvent.objects.filter(aggregate_type="door", aggregate_id="front", topic__in=DOOR_TOPICS)


def door_snapshot():
    state = DoorMonitorState.objects.filter(pk="front").first()
    if state is None:
        return {"door_id": "front", "state": "unknown", "is_open": None,
                "connected": False, "sequence": 0, "raw_status_hex": ""}
    return {"door_id": state.pk, "state": state.state,
            "is_open": True if state.state == "open" else False if state.state == "closed" else None,
            "connected": state.connected, "sequence": state.event_sequence,
            "raw_status_hex": state.raw_status_hex, "last_error": state.last_error,
            "last_observed_at": state.last_observed_at.isoformat() if state.last_observed_at else None}
