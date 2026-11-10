"""Deliver hardware-thread notifications on each subscriber's ASGI event loop.

The local InMemoryChannelLayer uses asyncio queues. A serial-port worker must
not write those queues from a different thread/event loop.
"""
import asyncio
import logging
import threading

from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_subscribers = {}


def subscribe(group, channel_name):
    with _lock:
        _subscribers.setdefault(group, {})[channel_name] = asyncio.get_running_loop()


def unsubscribe(group, channel_name):
    with _lock:
        channels = _subscribers.get(group)
        if channels is not None:
            channels.pop(channel_name, None)
            if not channels:
                _subscribers.pop(group, None)


async def _send(channels):
    try:
        layer = get_channel_layer()
        if layer is not None:
            for channel_name in channels:
                await layer.send(channel_name, {"type": "outbox.ready"})
    except Exception:
        # The committed outbox remains the replay source after reconnecting.
        logger.exception("No se pudo notificar un evento local por WebSocket.")


def publish_ready(group):
    with _lock:
        targets = {}
        for channel_name, loop in _subscribers.get(group, {}).items():
            targets.setdefault(loop, []).append(channel_name)
    for loop, channels in targets.items():
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(lambda names=tuple(channels): asyncio.create_task(_send(names)))
            except RuntimeError:
                pass  # A closing socket recovers its cursor on reconnect.
