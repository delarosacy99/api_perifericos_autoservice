from dataclasses import asdict

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.outbox.services import enqueue_event

from .drivers.base import DoorObservation
from .models import DoorMonitorState


def serialize_observation(observation: DoorObservation) -> dict:
    payload = asdict(observation)
    payload["observed_at"] = timezone.now()
    return payload


def _event_payload(
    state: DoorMonitorState,
    *,
    previous_state: str | None = None,
    initial_observation: bool = False,
) -> dict:
    payload = {
        "station_id": settings.STATION_ID,
        "door_id": state.door_id,
        "door_name": state.door_name,
        "coverage": state.coverage,
        "connected": state.connected,
        "state": state.state,
        "is_open": (
            True
            if state.state == DoorMonitorState.State.OPEN
            else False
            if state.state == DoorMonitorState.State.CLOSED
            else None
        ),
        "raw_status_hex": state.raw_status_hex,
        "initial_observation": initial_observation,
    }
    if previous_state is not None:
        payload["previous_state"] = previous_state
    return payload


@transaction.atomic
def record_door_observation(
    observation: DoorObservation,
    *,
    stable_state: str | None,
) -> DoorMonitorState:
    now = timezone.now()
    monitor_state = (
        DoorMonitorState.objects.select_for_update().filter(pk=observation.door_id).first()
    )
    created = monitor_state is None
    if created:
        monitor_state = DoorMonitorState(
            door_id=observation.door_id,
            door_name=observation.door_name,
            coverage=observation.coverage,
            connected=True,
            connection_changed_at=now,
        )

    was_connected = monitor_state.connected
    previous_state = monitor_state.state
    monitor_state.door_name = observation.door_name
    monitor_state.coverage = observation.coverage
    monitor_state.connected = True
    monitor_state.raw_status_hex = observation.raw_status_hex
    monitor_state.last_observed_at = now
    monitor_state.last_error = ""
    if not was_connected:
        monitor_state.connection_changed_at = now

    state_changed = (
        stable_state
        in {
            DoorMonitorState.State.OPEN,
            DoorMonitorState.State.CLOSED,
        }
        and stable_state != previous_state
    )
    if state_changed:
        monitor_state.state = stable_state
        monitor_state.state_changed_at = now

    monitor_state.save()

    if not created and not was_connected:
        enqueue_event(
            topic="door.front.recovered",
            aggregate_type="door",
            aggregate_id=monitor_state.door_id,
            payload=_event_payload(monitor_state),
        )

    if state_changed:
        initial_observation = previous_state == DoorMonitorState.State.UNKNOWN
        topic = (
            "door.front.snapshot"
            if initial_observation
            else "door.front.opened"
            if stable_state == DoorMonitorState.State.OPEN
            else "door.front.closed"
        )
        enqueue_event(
            topic=topic,
            aggregate_type="door",
            aggregate_id=monitor_state.door_id,
            payload=_event_payload(
                monitor_state,
                previous_state=previous_state,
                initial_observation=initial_observation,
            ),
        )
    return monitor_state


@transaction.atomic
def record_door_disconnection(*, door_id: str, error: str) -> DoorMonitorState:
    now = timezone.now()
    monitor_state = DoorMonitorState.objects.select_for_update().filter(pk=door_id).first()
    created = monitor_state is None
    if created:
        monitor_state = DoorMonitorState(
            door_id=door_id,
            door_name="Puerta frontal",
            coverage="front_door_only",
            connected=False,
            state=DoorMonitorState.State.UNKNOWN,
            connection_changed_at=now,
        )

    was_connected = monitor_state.connected
    monitor_state.connected = False
    monitor_state.last_error = error
    if created or was_connected:
        monitor_state.connection_changed_at = now
    monitor_state.save()

    if created or was_connected:
        enqueue_event(
            topic="door.front.disconnected",
            aggregate_type="door",
            aggregate_id=monitor_state.door_id,
            payload={**_event_payload(monitor_state), "error": error},
        )
    return monitor_state
