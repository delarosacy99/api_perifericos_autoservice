from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.urls import path
from django.utils import timezone

from apps.cash_acceptor.models import CashCollectionSession
from apps.outbox.models import OutboxEvent
from apps.station_cash.consumers import CashEventsConsumer
from apps.station_cash.models import AttentionBinding


@override_settings(CENTRAL_SYNC_ENABLED=True, CORS_ALLOWED_ORIGINS=["https://kiosk.example.test"])
class CashWebsocketTests(TransactionTestCase):
    def setUp(self):
        self.attention = uuid4()
        self.collection = CashCollectionSession.objects.create(idempotency_key=str(uuid4()), remote_session_id=str(self.attention), currency="USD")
        AttentionBinding.objects.create(attention_id=self.attention, collection=self.collection, heartbeat_at=timezone.now())
        self.first = OutboxEvent.objects.create(topic="cash.collection.created", aggregate_type="cash_collection", aggregate_id=str(self.collection.pk), payload={})
        self.second = OutboxEvent.objects.create(topic="station.cash.synced", aggregate_type="cash_collection", aggregate_id=str(self.collection.pk), payload={"event_id": str(uuid4())})
        self.app = URLRouter([path("ws/v1/cash/", CashEventsConsumer.as_asgi())])

    def connection(self, origin="https://kiosk.example.test"):
        return WebsocketCommunicator(self.app, "/ws/v1/cash/", headers=[(b"origin", origin.encode())])

    def test_rejects_unapproved_origin(self):
        async def run():
            ws = self.connection("https://evil.test")
            connected, code = await ws.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4403)
            await ws.disconnect()
        async_to_sync(run)()

    def test_wrong_key_and_malformed_cursor_close_before_snapshot(self):
        async def run():
            for key, cursor in (("wrong-key", None), (settings.LOCAL_API_KEY, "bad-cursor")):
                ws = self.connection()
                self.assertTrue((await ws.connect())[0])
                await ws.send_json_to({"type": "authenticate", "api_key": key, "attention_id": str(self.attention), "cursor": cursor})
                response = await ws.receive_output(timeout=2)
                self.assertEqual(response["type"], "websocket.close")
                self.assertEqual(response["code"], 4401)
                await ws.disconnect()
        async_to_sync(run)()

    def test_snapshot_replay_and_heartbeat_do_not_ack_financial_event(self):
        async def run():
            ws = self.connection()
            self.assertTrue((await ws.connect())[0])
            await ws.send_json_to({"type": "authenticate", "api_key": settings.LOCAL_API_KEY, "attention_id": str(self.attention), "cursor": str(self.first.pk)})
            snapshot = await ws.receive_json_from(timeout=2)
            self.assertEqual(snapshot["type"], "snapshot")
            self.assertEqual(snapshot["data"]["attention_id"], str(self.attention))
            replay = await ws.receive_json_from(timeout=2)
            self.assertEqual(replay["cursor"], str(self.second.pk))
            self.assertEqual(replay["topic"], "station.cash.synced")
            await ws.send_json_to({"type": "heartbeat"})
            self.assertEqual((await ws.receive_json_from(timeout=2))["type"], "heartbeat")
            await ws.disconnect()
        async_to_sync(run)()
        self.second.refresh_from_db()
        self.assertIsNone(self.second.delivered_at)

    def test_cursor_from_other_attention_is_rejected(self):
        unrelated = OutboxEvent.objects.create(topic="station.cash.synced", aggregate_type="cash_collection", aggregate_id=str(uuid4()), payload={})
        async def run():
            ws = self.connection()
            await ws.connect()
            await ws.send_json_to({"type": "authenticate", "api_key": settings.LOCAL_API_KEY, "attention_id": str(self.attention), "cursor": str(unrelated.pk)})
            self.assertEqual((await ws.receive_output(timeout=2))["code"], 4401)
            await ws.disconnect()
        async_to_sync(run)()
