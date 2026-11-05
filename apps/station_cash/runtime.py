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
            try:
                close_old_connections()
                worker_cycle()
            except CashSyncError as exc:
                logger.warning("Sincronización de efectivo pendiente: %s", exc.code)
            except Exception:
                from apps.cash_acceptor.runtime import get_cash_acceptor_poller
                get_cash_acceptor_poller().fail_closed()
                logger.exception("No se completó un ciclo de sincronización del billetero.")
            finally:
                close_old_connections()
            self.stop_event.wait(1)

    def stop(self):
        self.stop_event.set()
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
