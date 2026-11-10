import atexit
import logging
import os
import threading

from django.conf import settings
from django.db import close_old_connections

from .central import CashSyncError

logger = logging.getLogger(__name__)
_process_lock = None
_worker = None
_wake_event = threading.Event()


def wake_sync_worker():
    _wake_event.set()


def acquire_process_lock():
    global _process_lock
    if _process_lock is not None:
        return
    path = settings.BASE_DIR / "var" / "station-cash.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0"); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError("Ya hay otro agente controlando este billetero.") from None
    _process_lock = stream


class CashSyncWorker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="cash-sync", daemon=True)

    def run(self):
        from .services import worker_cycle
        while not self.stop_event.is_set():
            _wake_event.clear()
            retry_at = None
            try:
                close_old_connections()
                retry_at = worker_cycle()
            except CashSyncError as exc:
                logger.warning("Sincronización de efectivo pendiente: %s", exc.code)
                from datetime import timedelta
                from django.utils import timezone
                retry_at = timezone.now() + timedelta(seconds=5)
            except Exception:
                from apps.cash_acceptor.runtime import get_cash_acceptor_poller
                get_cash_acceptor_poller().fail_closed()
                logger.exception("No se completó un ciclo de sincronización del billetero.")
                from datetime import timedelta
                from django.utils import timezone
                retry_at = timezone.now() + timedelta(seconds=5)
            finally:
                close_old_connections()
            from django.utils import timezone
            timeout = max(0, (retry_at - timezone.now()).total_seconds()) if retry_at else None
            _wake_event.wait(timeout)

    def stop(self):
        self.stop_event.set()
        _wake_event.set()
        from .control import get_control_stream
        get_control_stream().stop()
        self.thread.join(timeout=2)
        from .services import STATION_LOCK, initialize_hardware
        if STATION_LOCK.acquire(timeout=2):
            try:
                initialize_hardware()
            except Exception:
                logger.error("El billetero requiere recuperación al siguiente inicio.")
            finally:
                STATION_LOCK.release()


def start_sync_worker():
    global _worker
    acquire_process_lock()
    if _worker is None:
        from .services import initialize_hardware
        try:
            initialize_hardware()
        except Exception:
            logger.exception("Recepción bloqueada al recuperar el billetero.")
        _worker = CashSyncWorker()
        _worker.thread.start()
        atexit.register(_worker.stop)
