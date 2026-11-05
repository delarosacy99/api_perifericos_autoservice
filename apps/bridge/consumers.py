import asyncio
import contextlib

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from .authentication import origin_allowed
from .errors import BridgeError
from .lease import verify_screen
from .models import StreamEvent
from .services import snapshot


@database_sync_to_async
def initial_state(token, cursor):
    lease = verify_screen(token)
    last = StreamEvent.objects.order_by("-pk").first()
    last_id = last.pk if last else 0
    # No cursor means a fresh connection. Reconnect with the last consumed integer cursor.
    cursor = last_id if cursor is None else max(lease.event_floor, min(cursor, last_id))
    return cursor, snapshot()


@database_sync_to_async
def read_events(token, cursor):
    verify_screen(token)
    return list(
        StreamEvent.objects.filter(pk__gt=cursor)
        .order_by("pk")
        .values("id", "topic", "payload")[:100]
    )


class StationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        headers = dict(self.scope.get("headers", []))
        if not settings.BRIDGE_ENABLED or not origin_allowed(headers.get(b"origin", b"").decode()):
            await self.close(code=4403)
            return
        self.token = None
        self.pump_task = None
        await self.accept()
        self.auth_timeout = asyncio.create_task(self._auth_timeout())

    async def _auth_timeout(self):
        await asyncio.sleep(5)
        if self.token is None:
            await self.close(code=4401)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.close(code=4400)
            return
        try:
            if self.token is None:
                if content.get("type") != "authenticate" or not isinstance(
                    content.get("token"), str
                ):
                    await self.close(code=4401)
                    return
                cursor = content.get("cursor")
                if cursor is not None and (
                    not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0
                ):
                    await self.close(code=4400)
                    return
                self.token = content["token"]
                self.cursor, state = await initial_state(self.token, cursor)
                self.auth_timeout.cancel()
                await self.send_json({"type": "snapshot", "cursor": self.cursor, "data": state})
                self.pump_task = asyncio.create_task(self._pump())
            elif content.get("type") == "heartbeat":
                await database_sync_to_async(verify_screen)(self.token, renew=True)
                await self.send_json({"type": "heartbeat", "alive": True})
            else:
                await self.send_json({"type": "error", "code": "http_commands_only"})
        except BridgeError:
            await self.close(code=4401)

        except Exception:
            await self.close(code=1011)

    async def _pump(self):
        try:
            while True:
                events = await read_events(self.token, self.cursor)
                for event in events:
                    await self.send_json(
                        {
                            "type": "event",
                            "cursor": event["id"],
                            "topic": event["topic"],
                            "data": event["payload"],
                        }
                    )
                    self.cursor = event["id"]
                await asyncio.sleep(0.5)
        except BridgeError:
            await self.close(code=4401)
        except Exception:
            await self.close(code=1011)

    async def disconnect(self, close_code):
        for task in (getattr(self, "auth_timeout", None), getattr(self, "pump_task", None)):
            if task and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        # The HTTP heartbeat can remain alive during a transient WS reconnect.
        # The independent watchdog inhibits cash when both transports stop renewing.
