from uuid import uuid4
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.urls import path

from apps.outbox.models import OutboxEvent
from ..consumers import StationEventsConsumer
from ..events import door_events
from ..services import record_door_observation
from .test_runtime import observation


@override_settings(CENTRAL_SYNC_ENABLED=False, CORS_ALLOWED_ORIGINS=["https://kiosk.example.test"])
class StationWebsocketTests(TransactionTestCase):
    def setUp(self):
        record_door_observation(observation("closed", "00"), stable_state="closed")
        self.first = door_events().get()
        self.app = URLRouter([path("ws/v1/station/events/", StationEventsConsumer.as_asgi())])

    def connection(self, origin="https://kiosk.example.test"):
        return WebsocketCommunicator(self.app, "/ws/v1/station/events/", headers=[(b"origin", origin.encode())])

    async def authenticate(self, ws, cursor=None):
        self.assertTrue((await ws.connect())[0])
        await ws.send_json_to({"type": "authenticate", "api_key": settings.LOCAL_API_KEY, "cursor": cursor})
        return await ws.receive_json_from(timeout=2)

    def test_snapshot_and_live_event_work_without_attention_or_central(self):
        async def run():
            ws = self.connection()
            initial = await self.authenticate(ws)
            self.assertEqual(initial["data"]["door"]["state"], "closed")
            self.assertEqual(initial["snapshot_sequence"], 1)
            await database_sync_to_async(record_door_observation)(observation("open", "01"), stable_state="open")
            event = await ws.receive_json_from(timeout=2)
            self.assertEqual(event["topic"], "door.front.opened")
            self.assertEqual(event["sequence"], 2)
            self.assertFalse(event["replayed"])
            await ws.send_json_to({"type": "heartbeat"})
            self.assertEqual((await ws.receive_json_from(timeout=2))["type"], "heartbeat")
            await ws.disconnect()
        async_to_sync(run)()
        self.assertFalse(OutboxEvent.objects.filter(delivered_at__isnull=False).exists())

    def test_reconnect_replays_missed_events_and_sends_current_snapshot(self):
        record_door_observation(observation("open", "01"), stable_state="open")
        record_door_observation(observation("closed", "00"), stable_state="closed")
        async def run():
            ws = self.connection()
            initial = await self.authenticate(ws, str(self.first.pk))
            self.assertEqual(initial["data"]["door"]["state"], "closed")
            self.assertEqual(initial["data"]["door"]["sequence"], 3)
            events = [await ws.receive_json_from(timeout=2), await ws.receive_json_from(timeout=2)]
            self.assertEqual([event["sequence"] for event in events], [2, 3])
            self.assertTrue(all(event["replayed"] for event in events))
            await ws.disconnect()
        async_to_sync(run)()

    def test_wrong_origin_and_wrong_or_unicode_key_are_rejected(self):
        async def run():
            ws = self.connection("https://evil.test")
            self.assertEqual(await ws.connect(), (False, 4403))
            await ws.disconnect()
            for key in ("wrong-key", "clave-é", None):
                ws = self.connection()
                await ws.connect()
                await ws.send_json_to({"type": "authenticate", "api_key": key})
                self.assertEqual((await ws.receive_output(timeout=2))["code"], 4401)
                await ws.disconnect()
        async_to_sync(run)()

    def test_cursor_cannot_reference_cash_or_missing_events(self):
        cash = OutboxEvent.objects.create(topic="cash.note.credited", aggregate_type="cash_collection",
                                         aggregate_id=str(uuid4()), payload={})
        async def run():
            for cursor in (str(cash.pk), str(uuid4()), "malformed"):
                ws = self.connection()
                result = await self.authenticate(ws, cursor)
                self.assertEqual(result["type"], "resync_required")
                self.assertEqual((await ws.receive_output(timeout=2))["code"], 4409)
                await ws.disconnect()
        async_to_sync(run)()
        cash.refresh_from_db()
        self.assertIsNone(cash.delivered_at)

    def test_unrelated_events_never_enter_station_stream(self):
        OutboxEvent.objects.create(topic="cash.note.credited", aggregate_type="cash_collection", aggregate_id="front", payload={})
        OutboxEvent.objects.create(topic="door.front.opened", aggregate_type="door", aggregate_id="back", payload={})
        async def run():
            ws = self.connection()
            await self.authenticate(ws)
            self.assertTrue(await ws.receive_nothing(timeout=.3))
            await ws.disconnect()
        async_to_sync(run)()
