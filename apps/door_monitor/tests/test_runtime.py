from django.test import TestCase

from apps.outbox.models import OutboxEvent

from ..drivers.base import DoorMonitorConnectionError, DoorObservation
from ..models import DoorMonitorState
from ..runtime import DoorMonitorPoller


def observation(state: str, raw_status_hex: str) -> DoorObservation:
    return DoorObservation(
        door_id="front",
        door_name="Puerta frontal",
        coverage="front_door_only",
        driver="fake",
        model="XL128 V1.2",
        connected=True,
        monitoring_only=True,
        state=state,
        is_open=state == "open" if state in {"open", "closed"} else None,
        raw_status_hex=raw_status_hex,
        sdk_documented_status=raw_status_hex == "01",
        physically_calibrated_status=state in {"open", "closed"},
        port="COM1",
        baud_rate=9600,
    )


class SequenceDoorDriver:
    def __init__(self, responses):
        self.responses = list(responses)

    def read_status(self):
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class DoorMonitorPollerTestCase(TestCase):
    def make_poller(self, responses):
        driver = SequenceDoorDriver(responses)
        return DoorMonitorPoller(
            driver_factory=lambda: driver,
            interval_seconds=0.01,
            debounce_samples=2,
            offline_after_failures=3,
            recovery_samples=2,
        )

    def test_initial_state_and_real_transitions_are_persisted_once(self):
        poller = self.make_poller(
            [
                observation("closed", "00"),
                observation("closed", "00"),
                observation("open", "01"),
                observation("open", "01"),
                observation("open", "01"),
                observation("closed", "00"),
                observation("closed", "00"),
            ]
        )

        for _index in range(7):
            self.assertTrue(poller.process_once())

        state = DoorMonitorState.objects.get(pk="front")
        self.assertTrue(state.connected)
        self.assertEqual(state.state, DoorMonitorState.State.CLOSED)
        self.assertEqual(state.raw_status_hex, "00")
        events = list(OutboxEvent.objects.all())
        self.assertCountEqual(
            [event.topic for event in events],
            ["door.front.snapshot", "door.front.opened", "door.front.closed"],
        )
        snapshot = OutboxEvent.objects.get(topic="door.front.snapshot")
        opened = OutboxEvent.objects.get(topic="door.front.opened")
        closed = OutboxEvent.objects.get(topic="door.front.closed")
        self.assertTrue(snapshot.payload["initial_observation"])
        self.assertEqual(opened.payload["previous_state"], "closed")
        self.assertEqual(closed.payload["previous_state"], "open")

    def test_single_noisy_sample_does_not_change_state(self):
        poller = self.make_poller(
            [
                observation("closed", "00"),
                observation("closed", "00"),
                observation("open", "01"),
                observation("closed", "00"),
            ]
        )

        for _index in range(4):
            poller.process_once()

        state = DoorMonitorState.objects.get(pk="front")
        self.assertEqual(state.state, DoorMonitorState.State.CLOSED)
        self.assertCountEqual(
            list(OutboxEvent.objects.values_list("topic", flat=True)),
            ["door.front.snapshot"],
        )

    def test_disconnect_and_recovery_require_configured_consecutive_samples(self):
        failure = DoorMonitorConnectionError("sin respuesta")
        poller = self.make_poller(
            [
                observation("closed", "00"),
                observation("closed", "00"),
                failure,
                failure,
                failure,
                failure,
                observation("closed", "00"),
                observation("closed", "00"),
            ]
        )

        for _index in range(8):
            poller.process_once()

        state = DoorMonitorState.objects.get(pk="front")
        self.assertTrue(state.connected)
        self.assertEqual(state.state, DoorMonitorState.State.CLOSED)
        self.assertEqual(state.last_error, "")
        self.assertCountEqual(
            list(OutboxEvent.objects.values_list("topic", flat=True)),
            [
                "door.front.snapshot",
                "door.front.disconnected",
                "door.front.recovered",
            ],
        )

    def test_unknown_reading_replaces_the_previous_stable_state(self):
        poller = self.make_poller([
            observation("closed", "00"), observation("closed", "00"),
            observation("unknown", "FF"), observation("unknown", "FF"),
        ])
        for _ in range(4):
            poller.process_once()
        state = DoorMonitorState.objects.get(pk="front")
        self.assertEqual(state.state, "unknown")
        event = OutboxEvent.objects.get(topic="door.front.unknown")
        self.assertIsNone(event.payload["is_open"])
        self.assertEqual(event.payload["sequence"], 2)

    def test_initial_unknown_state_is_published_without_claiming_closed(self):
        poller = self.make_poller([observation("unknown", "FF"), observation("unknown", "FF")])
        poller.process_once()
        poller.process_once()
        event = OutboxEvent.objects.get(topic="door.front.snapshot")
        self.assertEqual(event.payload["state"], "unknown")
        self.assertIsNone(event.payload["is_open"])
