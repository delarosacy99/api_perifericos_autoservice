import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.outbox.models import OutboxEvent
from apps.station_cash.central import CashSyncError, central_call, central_door_event
from ..models import DoorMonitorState
from ..services import record_door_observation, record_door_disconnection
from ..sync import sync_pending_door_events
from .test_runtime import observation


@override_settings(CENTRAL_SYNC_ENABLED=True)
class DoorDeliveryTests(TestCase):
    def setUp(self):
        record_door_observation(observation("closed", "00"), stable_state="closed")
        record_door_observation(observation("open", "01"), stable_state="open")
        self.received = {}
        self.calls = []

    def accept(self, data):
        self.calls.append(deepcopy(data))
        previous = self.received.setdefault(data["event_id"], deepcopy(data))
        self.assertEqual(previous, data)
        return {"accepted": True, "event_id": data["event_id"], "sequence": data["payload"]["sequence"]}

    def test_event_sequence_is_persistent_across_transitions_and_recovery(self):
        record_door_disconnection(door_id="front", error="Port unavailable")
        record_door_observation(observation("closed", "00"), stable_state="closed")
        events = list(OutboxEvent.objects.order_by("payload__sequence"))
        self.assertEqual([event.payload["sequence"] for event in events], [1, 2, 3, 4, 5])
        self.assertEqual(events[-2].topic, "door.front.recovered")
        self.assertEqual(events[-1].topic, "door.front.closed")
        self.assertEqual(DoorMonitorState.objects.get().event_sequence, 5)

    def test_sequence_and_state_roll_back_if_outbox_insert_fails(self):
        with patch("apps.door_monitor.services.enqueue_event", side_effect=DatabaseError("storage failure")):
            with self.assertRaises(DatabaseError):
                record_door_observation(observation("closed", "00"), stable_state="closed")
        state = DoorMonitorState.objects.get()
        self.assertEqual(state.event_sequence, 2)
        self.assertEqual(state.state, "open")
        self.assertEqual(OutboxEvent.objects.count(), 2)

    def test_offline_keeps_events_in_order_and_persists_retry_time(self):
        with patch("apps.door_monitor.sync.central_door_event", side_effect=CashSyncError("central_unavailable")) as send:
            self.assertEqual(sync_pending_door_events(), 0)
            self.assertEqual(sync_pending_door_events(), 0)
            self.assertEqual(send.call_count, 1)
        first = OutboxEvent.objects.order_by("payload__sequence").first()
        self.assertEqual(first.attempts, 1)
        self.assertEqual(first.last_error, "central_unavailable")
        self.assertIsNotNone(first.retry_at)
        self.assertFalse(OutboxEvent.objects.filter(delivered_at__isnull=False).exists())
        self.assertEqual(DoorMonitorState.objects.get().state, "open")
        OutboxEvent.objects.update(retry_at=timezone.now()-timedelta(seconds=1))
        with patch("apps.door_monitor.sync.central_door_event", side_effect=self.accept):
            self.assertEqual(sync_pending_door_events(), 2)
        self.assertEqual([call["payload"]["sequence"] for call in self.calls], [1, 2])

    def test_lost_response_retries_exact_identity_and_payload(self):
        def lost(data):
            self.accept(data)
            raise CashSyncError("central_unavailable")
        with patch("apps.door_monitor.sync.central_door_event", side_effect=lost):
            sync_pending_door_events()
        OutboxEvent.objects.update(retry_at=None)
        with patch("apps.door_monitor.sync.central_door_event", side_effect=self.accept):
            self.assertEqual(sync_pending_door_events(), 2)
        self.assertEqual(len(self.received), 2)
        self.assertEqual(self.calls[0], self.calls[1])
        self.assertFalse(OutboxEvent.objects.filter(delivered_at__isnull=True).exists())

    def test_delivery_does_not_touch_cash_or_unrelated_events(self):
        cash = OutboxEvent.objects.create(topic="cash.note.credited", aggregate_type="cash_collection",
                                         aggregate_id=str(uuid4()), payload={"amount_minor": 500})
        unrelated = OutboxEvent.objects.create(topic="other.event", aggregate_type="door", aggregate_id="front", payload={})
        with patch("apps.door_monitor.sync.central_door_event", side_effect=self.accept):
            self.assertEqual(sync_pending_door_events(limit=1), 1)
        cash.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertIsNone(cash.delivered_at)
        self.assertIsNone(unrelated.delivered_at)
        self.assertEqual(cash.attempts, 0)

    def test_wrong_confirmation_does_not_mark_event_delivered(self):
        for response in ({"accepted": False, "sequence": 1}, {"accepted": True, "sequence": 999}):
            OutboxEvent.objects.update(retry_at=None)
            with patch("apps.door_monitor.sync.central_door_event", return_value=response):
                self.assertEqual(sync_pending_door_events(), 0)
        self.assertFalse(OutboxEvent.objects.filter(delivered_at__isnull=False).exists())

    @override_settings(CENTRAL_SYNC_ENABLED=False)
    def test_disabled_sync_performs_no_network_call(self):
        with patch("apps.door_monitor.sync.central_door_event") as send:
            self.assertEqual(sync_pending_door_events(), 0)
            send.assert_not_called()


@override_settings(CENTRAL_SYNC_ENABLED=True, CENTRAL_AGENT_CREDENTIAL="38.test-agent-credential-long",
                   CENTRAL_API_BASE_URL="https://central.example.test/api/organizacion/autoservice/", CENTRAL_TIMEOUT_SECONDS=8)
class DoorTransportTests(TestCase):
    def response(self, value):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(value).encode()
        return response

    def test_fixed_route_authentication_and_cash_transport_still_work(self):
        event_id, attention_id = str(uuid4()), str(uuid4())
        with patch("apps.station_cash.central.build_opener") as build:
            opener = build.return_value
            opener.open.return_value = self.response({"event_id": event_id, "accepted": True, "sequence": 1})
            central_door_event({"event_id": event_id})
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, settings.CENTRAL_API_BASE_URL + "agent/device-events/door/")
            self.assertEqual(request.get_header("Authorization"), "AutoserviceAgent " + settings.CENTRAL_AGENT_CREDENTIAL)
            self.assertEqual(request.get_method(), "POST")
            opener.open.return_value = self.response({"attention_id": attention_id})
            central_call(attention_id)
            self.assertEqual(opener.open.call_args.args[0].get_method(), "GET")

    def test_response_for_another_event_is_rejected(self):
        with patch("apps.station_cash.central.build_opener") as build:
            build.return_value.open.return_value = self.response({"event_id": str(uuid4())})
            with self.assertRaisesMessage(CashSyncError, "No se confirmó"):
                central_door_event({"event_id": str(uuid4())})

    @override_settings(DEBUG=False, CENTRAL_API_BASE_URL="http://central.example.test/")
    def test_plain_http_is_rejected_before_network(self):
        with patch("apps.station_cash.central.build_opener") as build:
            with self.assertRaises(CashSyncError) as error:
                central_door_event({"event_id": str(uuid4())})
            self.assertEqual(error.exception.code, "central_https_required")
            build.assert_not_called()
