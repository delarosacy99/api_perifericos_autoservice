import uuid

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.urls import path

from apps.bridge.consumers import StationConsumer
from apps.bridge.lease import claim_screen
from apps.bridge.models import StreamEvent


@override_settings(BRIDGE_ENABLED=True, CORS_ALLOWED_ORIGINS=["https://kiosk.example"])
class WebSocketTests(TransactionTestCase):
    def setUp(self):
        self.token = claim_screen(uuid.uuid4())["screen_token"]
        self.app = URLRouter([path("ws/v1/bridge/", StationConsumer.as_asgi())])

    def connection(self, origin="https://kiosk.example"):
        return WebsocketCommunicator(
            self.app, "/ws/v1/bridge/", headers=[(b"origin", origin.encode())]
        )

    def test_rejects_cross_origin_before_accept(self):
        async def scenario():
            ws = self.connection("https://evil.example")
            accepted, code = await ws.connect()
            self.assertFalse(accepted)
            self.assertEqual(code, 4403)
            await ws.disconnect()

        async_to_sync(scenario)()

    def test_authentication_snapshot_and_heartbeat(self):
        async def scenario():
            ws = self.connection()
            self.assertTrue((await ws.connect())[0])
            await ws.send_json_to({"type": "authenticate", "token": self.token})
            message = await ws.receive_json_from()
            self.assertEqual(message["type"], "snapshot")
            self.assertNotIn("token", str(message))
            await ws.send_json_to({"type": "heartbeat"})
            self.assertTrue((await ws.receive_json_from())["alive"])
            await ws.disconnect()

        async_to_sync(scenario)()

    def test_invalid_token_closes_without_snapshot(self):
        async def scenario():
            ws = self.connection()
            await ws.connect()
            await ws.send_json_to({"type": "authenticate", "token": "wrong"})
            self.assertEqual((await ws.receive_output())["code"], 4401)
            await ws.disconnect()

        async_to_sync(scenario)()

    def test_replays_persisted_events_from_cursor(self):
        event = StreamEvent.objects.create(
            topic="bridge.cash.synced", payload={"amount_minor": 200}
        )

        async def scenario():
            ws = self.connection()
            await ws.connect()
            await ws.send_json_to(
                {"type": "authenticate", "token": self.token, "cursor": event.pk - 1}
            )
            await ws.receive_json_from()
            replay = await ws.receive_json_from()
            self.assertEqual(replay["cursor"], event.pk)
            self.assertEqual(replay["topic"], event.topic)
            await ws.disconnect()

        async_to_sync(scenario)()

    def test_websocket_cannot_create_financial_operations(self):
        async def scenario():
            ws = self.connection()
            await ws.connect()
            await ws.send_json_to({"type": "authenticate", "token": self.token})
            await ws.receive_json_from()
            await ws.send_json_to({"type": "cash", "amount_minor": 10000})
            self.assertEqual((await ws.receive_json_from())["code"], "http_commands_only")
            await ws.disconnect()

        async_to_sync(scenario)()

    def test_new_screen_does_not_replay_previous_customer_events(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.bridge.models import ScreenLease

        old = StreamEvent.objects.create(topic="private.old", payload={"amount_minor": 123})
        ScreenLease.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        token = claim_screen(uuid.uuid4())["screen_token"]

        async def scenario():
            ws = self.connection()
            await ws.connect()
            await ws.send_json_to({"type": "authenticate", "token": token, "cursor": 0})
            message = await ws.receive_json_from()
            self.assertGreaterEqual(message["cursor"], old.pk)
            self.assertTrue(await ws.receive_nothing(timeout=0.02))
            await ws.disconnect()

        async_to_sync(scenario)()
