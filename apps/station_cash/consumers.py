import asyncio
import contextlib
import hmac
from uuid import UUID

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.db.models import Q

from apps.outbox.models import OutboxEvent
from apps.outbox.realtime import subscribe, unsubscribe
from .central import CashSyncError
from .services import binding_for, heartbeat, snapshot


@database_sync_to_async
def connect_state(attention_id, cursor):
    binding = binding_for(attention_id)
    heartbeat(attention_id)
    query = OutboxEvent.objects.filter(aggregate_id=str(binding.collection_id)).order_by("occurred_at", "id")
    if cursor:
        cursor = UUID(str(cursor))
        previous = query.filter(pk=cursor).first()
        if previous is None:
            raise CashSyncError("invalid_event_cursor")
    else:
        previous = query.last()
    return snapshot(binding), (previous.occurred_at.isoformat(), str(previous.pk)) if previous else None


@database_sync_to_async
def events_after(attention_id, cursor):
    binding = binding_for(attention_id)
    events = OutboxEvent.objects.filter(aggregate_id=str(binding.collection_id)).order_by("occurred_at", "id")
    if cursor:
        events = events.filter(Q(occurred_at__gt=cursor[0]) | Q(occurred_at=cursor[0], id__gt=cursor[1]))
    return list(events.values("id", "topic", "payload", "occurred_at")[:100])


class CashEventsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        origin = dict(self.scope.get("headers", [])).get(b"origin", b"").decode()
        if not settings.CENTRAL_SYNC_ENABLED or origin not in settings.CORS_ALLOWED_ORIGINS:
            await self.close(code=4403); return
        self.authenticated = False
        self.pump_task = None
        await self.accept()
        self.timeout_task = asyncio.create_task(self.auth_timeout())

    async def auth_timeout(self):
        await asyncio.sleep(5)
        if not self.authenticated:
            await self.close(code=4401)

    async def receive_json(self, content, **kwargs):
        try:
            if not isinstance(content, dict):
                raise ValueError
            if not self.authenticated:
                key = content.get("api_key", "")
                if content.get("type") != "authenticate" or not isinstance(key, str) or not hmac.compare_digest(key, settings.LOCAL_API_KEY):
                    raise ValueError
                self.attention_id = UUID(content["attention_id"])
                state, self.cursor = await connect_state(self.attention_id, content.get("cursor"))
                self.authenticated = True
                self.timeout_task.cancel()
                self.group_name = "cash." + state["collection_id"]
                await self.channel_layer.group_add(self.group_name, self.channel_name)
                subscribe(self.group_name, self.channel_name)
                await self.send_json({"type": "snapshot", "cursor": self.cursor[1] if self.cursor else None, "data": state})
                await self.flush_events()
            elif content.get("type") == "heartbeat":
                await database_sync_to_async(heartbeat)(self.attention_id)
                await self.channel_layer.group_add(self.group_name, self.channel_name)
                await self.send_json({"type": "heartbeat", "alive": True})
        except (ValueError, KeyError, TypeError, CashSyncError):
            await self.close(code=4401)

    async def flush_events(self):
        while True:
            events = await events_after(self.attention_id, self.cursor)
            for event in events:
                await self.send_json({"type": "event", "cursor": str(event["id"]), "topic": event["topic"], "data": event["payload"]})
                self.cursor = (event["occurred_at"].isoformat(), str(event["id"]))
            if len(events) < 100:
                return

    async def outbox_ready(self, event):
        await self.flush_events()

    async def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            unsubscribe(self.group_name, self.channel_name)
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        for task in (getattr(self, "timeout_task", None), getattr(self, "pump_task", None)):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
