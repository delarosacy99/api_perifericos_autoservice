import atexit
import logging
import os
import threading

from django.conf import settings
from django.db import close_old_connections

from .errors import BridgeError

logger = logging.getLogger(__name__)
_lock_file = None
_worker = None


def acquire_process_lock():
    """OS releases lock after a process crash; prevents two servers controlling the NV200."""
    global _lock_file
    if _lock_file is not None:
        return
    path = settings.BASE_DIR / "var" / "bridge.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError("Ya hay otro agente controlando esta estación.") from None
    _lock_file = stream


class BridgeWorker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name="station-bridge", daemon=True)
        self.thread.start()

    def run(self):
        from .services import worker_cycle

        while not self.stop_event.is_set():
            try:
                close_old_connections()
                worker_cycle()
            except BridgeError as exc:
                # Codes only: never print requests, tokens, payloads or central responses.
                logger.warning("Puente de estación pendiente: %s", exc.code)
            except Exception:
                logger.exception("Falló un ciclo del puente de estación.")
            finally:
                close_old_connections()
            self.stop_event.wait(settings.BRIDGE_WORKER_INTERVAL_SECONDS)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        # Best effort: a durable attention remains recoverable if the OS stops us first.
        from .lease import BRIDGE_LOCK
        from .services import inhibit_cash

        acquired = BRIDGE_LOCK.acquire(timeout=2)
        if acquired:
            try:
                inhibit_cash()
            except Exception:
                logger.error("El cierre del billetero requiere recuperación al iniciar.")
            finally:
                BRIDGE_LOCK.release()


def start_bridge():
    global _worker
    acquire_process_lock()
    if _worker is None:
        from .lease import BRIDGE_LOCK
        from .models import ScreenLease
        from .services import inhibit_cash

        # Finish startup recovery before the ASGI server can accept a screen.
        with BRIDGE_LOCK:
            ScreenLease.objects.filter(pk=1).update(expires_at=None, token_hash="")
            from apps.cash_acceptor.drivers.base import CashAcceptorError

            try:
                inhibit_cash()
            except CashAcceptorError as exc:
                logger.error("Billetero bloqueado durante recuperación: %s", exc.code)
        _worker = BridgeWorker()
        _worker.start()
        atexit.register(_worker.stop)
