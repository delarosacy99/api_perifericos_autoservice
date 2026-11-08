"""Station-wide local events, authenticated without a cash attention."""
import asyncio
import contextlib
import hmac
from uuid import UUID

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.db import transaction

from .events import door_events, door_snapshot
from .models import DoorMonitorState


@database_sync_to_async
def connect_state(cursor):
    with transaction.atomic():
        # The producer locks this same row for the state + event transaction.
        state = DoorMonitorState.objects.select_for_update().filter(pk="front").first()
        latest = door_events().order_by("-payload__sequence").first()
        previous = None
        if cursor is not None:
            previous = door_events().filter(pk=UUID(str(cursor))).first()
            if previous is None:
                raise ValueError("invalid_event_cursor")
        else:
            previous = latest
        return {"door": door_snapshot(), "monitoring_enabled": settings.DOOR_MONITOR_BACKGROUND_ENABLED}, previous, latest


@database_sync_to_async
def events_after(sequence):
    return list(door_events().filter(payload__sequence__gt=sequence)
                .order_by("payload__sequence").values("id", "topic", "payload", "occurred_at")[:100])


class StationEventsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        origin = dict(self.scope.get("headers", [])).get(b"origin", b"").decode()
        if not origin or origin not in settings.CORS_ALLOWED_ORIGINS:
            await self.close(code=4403)
            return
        self.authenticated = False
        self.pump_task = None
        await self.accept()
        self.timeout_task = asyncio.create_task(self.auth_timeout())

    async def auth_timeout(self):
        await asyncio.sleep(5)
        if not self.authenticated:
            await self.close(code=4401)

    async def receive_json(self, content, **kwargs):
        if not self.authenticated:
            key = content.get("api_key") if isinstance(content, dict) else None
            if (not isinstance(content, dict) or content.get("type") != "authenticate"
                    or not isinstance(key, str) or not settings.LOCAL_API_KEY
                    or not hmac.compare_digest(key.encode("utf-8"), settings.LOCAL_API_KEY.encode("utf-8"))):
                await self.close(code=4401)
                return
            try:
                snapshot, previous, latest = await connect_state(content.get("cursor"))
            except (ValueError, TypeError, AttributeError):
                await self.send_json({"type": "resync_required", "code": "invalid_event_cursor"})
                await self.close(code=4409)
                return
            self.sequence = previous.payload["sequence"] if previous else 0
            self.snapshot_sequence = latest.payload["sequence"] if latest else 0
            self.authenticated = True
            self.timeout_task.cancel()
            await self.send_json({"type": "snapshot", "cursor": str(previous.pk) if previous else None,
                                  "snapshot_sequence": self.snapshot_sequence, "data": snapshot})
            self.pump_task = asyncio.create_task(self.pump())
        elif isinstance(content, dict) and content.get("type") == "heartbeat":
            await self.send_json({"type": "heartbeat", "alive": True})

    async def pump(self):
        try:
            while True:
                for event in await events_after(self.sequence):
                    sequence = event["payload"]["sequence"]
                    await self.send_json({"type": "event", "cursor": str(event["id"]),
                                          "sequence": sequence, "topic": event["topic"],
                                          "occurred_at": event["occurred_at"].isoformat(),
                                          "replayed": sequence <= self.snapshot_sequence,
                                          "data": event["payload"]})
                    self.sequence = sequence
                await asyncio.sleep(.25)
        except Exception:
            await self.close(code=1011)

    async def disconnect(self, close_code):
        for task in (getattr(self, "timeout_task", None), getattr(self, "pump_task", None)):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
