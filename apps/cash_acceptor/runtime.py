import logging
import threading
from collections.abc import Callable

from django.conf import settings
from django.db import close_old_connections

from .drivers import get_cash_acceptor
from .models import CashCollectionSession

logger = logging.getLogger(__name__)

# Serializes a complete POLL -> database commit -> EVENT ACK cycle with API start/stop.
CASH_ACCEPTOR_OPERATION_LOCK = threading.RLock()


class CashAcceptorPoller:
    def __init__(
        self,
        *,
        driver_factory: Callable = get_cash_acceptor,
        interval_seconds: float | None = None,
    ) -> None:
        self._driver_factory = driver_factory
        self._interval_seconds = (
            settings.NV200_POLL_INTERVAL_SECONDS if interval_seconds is None else interval_seconds
        )
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self.faulted = False

    def fail_closed(self):
        self.faulted = True
        try:
            self._driver_factory().disable()
        except Exception:
            logger.error("No fue posible confirmar la inhibición física del billetero.")

    def resume(self):
        # Called only after a new collection has committed successfully.
        self.faulted = False
        self.ensure_running()

    def ensure_running(self) -> None:
        driver = self._driver_factory()
        if not driver.supports_polling:
            return
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake_event.set()
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="cash-acceptor-poller",
                daemon=True,
            )
            self._thread.start()

    def wake(self) -> None:
        self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def process_once(self) -> bool:
        """Poll once. Returns True when the driver was actively polled."""

        driver = self._driver_factory()
        if not driver.supports_polling:
            return False

        with CASH_ACCEPTOR_OPERATION_LOCK:
            if self.faulted:
                driver.disable()
                return False
            collection = CashCollectionSession.objects.filter(
                status=CashCollectionSession.Status.ACCEPTING
            ).first()
            if collection is None:
                if driver.snapshot().accepting:
                    logger.error(
                        "NV200 activo sin una recolección local; se deshabilitará por seguridad."
                    )
                    driver.disable()
                return False

            snapshot = driver.snapshot()
            if settings.CENTRAL_SYNC_ENABLED:
                from datetime import timedelta
                from django.utils import timezone
                from apps.station_cash.models import AttentionBinding
                binding = AttentionBinding.objects.filter(collection=collection, sealed=False).first()
                if (not binding or binding.last_error or collection.metadata.get("inhibit_requested")
                    or binding.heartbeat_at < timezone.now() - timedelta(seconds=settings.FRONTEND_HEARTBEAT_TIMEOUT_SECONDS)):
                    from .services import stop_collection
                    stop_collection(collection.pk, reason="frontend_disconnected")
                    return False
            if not snapshot.connected or not snapshot.accepting:
                from .services import connect_and_enable_collection_hardware

                connect_and_enable_collection_hardware(collection, driver=driver)
                logger.info(
                    "Conexión con NV200 recuperada para la recolección %s.",
                    collection.id,
                )

            batch = driver.poll_once()
            # Imported here to avoid coupling driver initialization to Django service imports.
            from .services import record_note_credit, stop_collection

            for note_credit in batch.note_credits:
                record_note_credit(
                    collection_id=collection.id,
                    note_event=note_credit,
                )
            if batch.requires_ack:
                driver.acknowledge_events(batch.batch_id)

            cashbox_removed = "CASHBOX_REMOVED" in batch.state_changes
            for state_change in batch.state_changes:
                if state_change == "CASHBOX_REMOVED":
                    logger.error("Alerta NV200: CASHBOX_REMOVED (código 0xE3)")
                elif state_change == "CASHBOX_REPLACED":
                    logger.info("Estado NV200: CASHBOX_REPLACED (código 0xE4)")

            for event in batch.events:
                if event.name in {
                    "SAFE_NOTE_JAM",
                    "UNSAFE_NOTE_JAM",
                    "STACKER_FULL",
                    "NOTE_PATH_OPEN",
                    "BARCODE_TICKET_VALIDATED",
                    "BARCODE_TICKET_ACK",
                }:
                    logger.error(
                        "Alerta NV200: %s (código 0x%02X)",
                        event.name,
                        event.code,
                    )

            if cashbox_removed:
                stop_collection(
                    collection.id,
                    reason=CashCollectionSession.StopReason.DEVICE_ERROR,
                )
                logger.error(
                    "Recolección %s detenida porque el cashbox fue retirado.",
                    collection.id,
                )
            return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                close_old_connections()
                was_polled = self.process_once()
            except Exception:
                if settings.CENTRAL_SYNC_ENABLED:
                    self.fail_closed()
                logger.exception("Falló un ciclo de polling del NV200.")
                was_polled = False
            finally:
                close_old_connections()

            delay = self._interval_seconds if was_polled else 0.5
            self._wake_event.wait(delay)
            self._wake_event.clear()


_poller: CashAcceptorPoller | None = None
_poller_lock = threading.Lock()


def get_cash_acceptor_poller() -> CashAcceptorPoller:
    global _poller
    with _poller_lock:
        if _poller is None:
            _poller = CashAcceptorPoller()
        return _poller


def reset_cash_acceptor_poller() -> None:
    global _poller
    with _poller_lock:
        if _poller is not None:
            _poller.stop()
        _poller = None
