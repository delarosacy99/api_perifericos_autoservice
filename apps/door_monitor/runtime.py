import logging
import threading
from collections.abc import Callable

from django.conf import settings
from django.db import close_old_connections

from .drivers import get_door_monitor
from .drivers.base import DoorMonitorError, DoorObservation
from .services import record_door_disconnection, record_door_observation

logger = logging.getLogger(__name__)


class DoorMonitorPoller:
    def __init__(
        self,
        *,
        driver_factory: Callable = get_door_monitor,
        interval_seconds: float | None = None,
        debounce_samples: int | None = None,
        offline_after_failures: int | None = None,
        recovery_samples: int | None = None,
    ) -> None:
        self._driver_factory = driver_factory
        self._interval_seconds = (
            settings.DOOR_MONITOR_POLL_INTERVAL_SECONDS
            if interval_seconds is None
            else interval_seconds
        )
        self._debounce_samples = (
            settings.DOOR_MONITOR_DEBOUNCE_SAMPLES if debounce_samples is None else debounce_samples
        )
        self._offline_after_failures = (
            settings.DOOR_MONITOR_OFFLINE_AFTER_FAILURES
            if offline_after_failures is None
            else offline_after_failures
        )
        self._recovery_samples = (
            settings.DOOR_MONITOR_RECOVERY_SAMPLES if recovery_samples is None else recovery_samples
        )
        self._candidate_state: str | None = None
        self._candidate_count = 0
        self._published_state: str | None = None
        self._failure_count = 0
        self._success_count = 0
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

    def ensure_running(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake_event.set()
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="door-monitor-poller",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def process_once(self) -> bool:
        try:
            observation = self._driver_factory().read_status()
        except DoorMonitorError as exc:
            self._record_failure(exc)
            return False

        self._record_success(observation)
        return True

    def _record_failure(self, exc: DoorMonitorError) -> None:
        self._success_count = 0
        self._failure_count += 1
        self._candidate_state = None
        self._candidate_count = 0
        if self._failure_count == self._offline_after_failures:
            record_door_disconnection(door_id="front", error=str(exc))
            logger.error(
                "Monitor XL128 desconectado después de %s fallos consecutivos: %s",
                self._failure_count,
                exc,
            )

    def _record_success(self, observation: DoorObservation) -> None:
        self._failure_count = 0
        self._success_count += 1

        if observation.state in {"open", "closed"}:
            if observation.state == self._candidate_state:
                self._candidate_count += 1
            else:
                self._candidate_state = observation.state
                self._candidate_count = 1
        else:
            self._candidate_state = None
            self._candidate_count = 0

        connection_confirmed = self._success_count == self._recovery_samples
        stable_candidate = (
            observation.state
            if self._success_count >= self._recovery_samples
            and self._candidate_count >= self._debounce_samples
            else None
        )
        state_needs_record = (
            stable_candidate is not None and stable_candidate != self._published_state
        )
        if connection_confirmed or state_needs_record:
            record_door_observation(
                observation,
                stable_state=stable_candidate if state_needs_record else None,
            )
            if state_needs_record:
                self._published_state = stable_candidate

    def _run(self) -> None:
        logger.info("Monitoreo continuo de la puerta frontal iniciado.")
        while not self._stop_event.is_set():
            try:
                close_old_connections()
                self.process_once()
            except Exception:
                logger.exception("Falló un ciclo del monitor de puerta frontal.")
            finally:
                close_old_connections()

            self._wake_event.wait(self._interval_seconds)
            self._wake_event.clear()


_poller: DoorMonitorPoller | None = None
_poller_lock = threading.Lock()


def get_door_monitor_poller() -> DoorMonitorPoller:
    global _poller
    with _poller_lock:
        if _poller is None:
            _poller = DoorMonitorPoller()
        return _poller


def reset_door_monitor_poller() -> None:
    global _poller
    with _poller_lock:
        if _poller is not None:
            _poller.stop()
        _poller = None
