"""Ordered, retryable delivery. WebSocket readers never acknowledge this queue."""
import atexit
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from apps.station_cash.central import CashSyncError, central_door_event
from .events import door_events

logger = logging.getLogger(__name__)
_sync_lock = threading.Lock()
_worker = None
_wake_event = threading.Event()


def wake_door_sync():
    _wake_event.set()


def sync_pending_door_events(*, limit=50):
    if not settings.CENTRAL_SYNC_ENABLED:
        return 0
    delivered = 0
    # A single application process owns station-cash.lock. This lock also prevents
    # two calls within that process from delivering the same queue concurrently.
    with _sync_lock:
        for _ in range(limit):
            event = door_events().filter(delivered_at__isnull=True).order_by("payload__sequence").first()
            if event is None or (event.retry_at and event.retry_at > timezone.now()):
                break
            event.attempts += 1
            event.save(update_fields=["attempts"])
            try:
                result = central_door_event({"event_id": str(event.pk), "topic": event.topic,
                                            "occurred_at": event.occurred_at.isoformat(), "payload": event.payload})
                if result.get("accepted") is not True or result.get("sequence") != event.payload.get("sequence"):
                    raise CashSyncError("central_door_event_not_confirmed")
            except CashSyncError as exc:
                changed = event.last_error != exc.code
                event.last_error = exc.code
                event.retry_at = timezone.now() + timedelta(seconds=min(30, 2 ** min(event.attempts, 5)))
                event.save(update_fields=["last_error", "retry_at"])
                if changed:
                    logger.warning("Evento de puerta pendiente de sincronización: %s", exc.code)
                break
            event.delivered_at = timezone.now()
            event.retry_at = None
            event.last_error = ""
            event.save(update_fields=["delivered_at", "last_error", "retry_at"])
            delivered += 1
    return delivered


class DoorSyncWorker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="door-event-sync", daemon=True)

    def run(self):
        while not self.stop_event.is_set():
            _wake_event.clear()
            timeout = None
            try:
                close_old_connections()
                sync_pending_door_events()
                pending = door_events().filter(delivered_at__isnull=True).order_by("payload__sequence").first()
                if pending is not None:
                    timeout = max(0, (pending.retry_at - timezone.now()).total_seconds()) if pending.retry_at else 0
            except Exception:
                # Device telemetry failures do not alter cash receipt or ACKs.
                logger.exception("No se completó la sincronización de eventos de puerta.")
                timeout = 5
            finally:
                close_old_connections()
            _wake_event.wait(timeout)

    def stop(self):
        self.stop_event.set()
        _wake_event.set()
        self.thread.join(timeout=2)


def start_door_sync_worker():
    global _worker
    from apps.station_cash.runtime import acquire_process_lock
    acquire_process_lock()
    if _worker is None:
        _worker = DoorSyncWorker()
        _worker.thread.start()
        atexit.register(_worker.stop)
