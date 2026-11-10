"""A live, authenticated permission channel; no repeated HTTP permission poll."""
import json
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from django.conf import settings
from websockets.sync.client import connect

from .central import CashSyncError


def control_url():
    base = urlsplit(settings.CENTRAL_API_BASE_URL)
    configured = getattr(settings, "CENTRAL_CONTROL_WS_URL", "")
    value = configured or urlunsplit(("wss" if base.scheme == "https" else "ws", base.netloc,
                                      "/ws/autoservice/agent/control/", "", ""))
    url = urlsplit(value)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or (url.scheme != "wss" and not (settings.DEBUG and url.scheme == "ws"
                and url.hostname in {"127.0.0.1", "localhost", "::1"}))):
        raise CashSyncError("invalid_central_control_url")
    return value


class CentralControlStream:
    heartbeat_seconds = 5
    silence_seconds = 12

    def __init__(self):
        self.attention_id = None
        self.error = "central_control_disconnected"
        self._last_live = 0
        self._authorized = False
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._connection = None

    def is_authorized(self, attention_id):
        return (str(attention_id) == self.attention_id and self._authorized
                and time.monotonic() - self._last_live < self.silence_seconds)

    def authorize(self, attention_id):
        if self.is_authorized(attention_id):
            return
        self.stop()
        self.attention_id = str(attention_id)
        self.error = "central_control_unavailable"
        self._authorized = False
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, self.attention_id, self._ready),
                                        name="central-control", daemon=True)
        self._thread.start()
        if not self._ready.wait(settings.CENTRAL_TIMEOUT_SECONDS + 1) or not self.is_authorized(attention_id):
            self.stop()
            raise CashSyncError(self.error)

    def deny(self, code="central_control_disconnected"):
        self._authorized = False
        self.error = code
        self._ready.set()
        # Inhibit physically before any potentially slow database operation.
        from apps.cash_acceptor.runtime import get_cash_acceptor_poller
        get_cash_acceptor_poller().fail_closed()
        from .runtime import wake_sync_worker
        wake_sync_worker()

    def _run(self, stop_event, attention_id, ready):
        connection = None
        try:
            url = control_url()
            options = {"ssl": ssl.create_default_context()} if url.startswith("wss:") else {}
            with connect(url, open_timeout=settings.CENTRAL_TIMEOUT_SECONDS,
                         close_timeout=1, ping_interval=None, compression=None,
                         max_size=65536, proxy=None, **options) as connection:
                if stop_event.is_set():
                    return
                self._connection = connection
                connection.send(json.dumps({"type": "authenticate", "credential": settings.CENTRAL_AGENT_CREDENTIAL,
                                            "attention_id": attention_id}))
                pending_nonce = None
                next_heartbeat = time.monotonic() + self.heartbeat_seconds
                opened = time.monotonic()
                while not stop_event.is_set():
                    now = time.monotonic()
                    if now - (self._last_live if self._authorized else opened) >= self.silence_seconds:
                        raise CashSyncError("central_control_timeout")
                    if self._authorized and now >= next_heartbeat and pending_nonce is None:
                        pending_nonce = str(uuid4())
                        connection.send(json.dumps({"type": "heartbeat", "nonce": pending_nonce}))
                        next_heartbeat = now + self.heartbeat_seconds
                    try:
                        deadline = next_heartbeat if pending_nonce is None and self._authorized else now + 1
                        timeout = min(1, max(.001, deadline - now))
                        message = json.loads(connection.recv(timeout=timeout))
                    except TimeoutError:
                        continue
                    if stop_event.is_set():
                        return
                    if not isinstance(message, dict):
                        raise CashSyncError("central_control_invalid_message")
                    kind = message.get("type")
                    if kind == "collection.authorized":
                        expected_terminal = settings.CENTRAL_AGENT_CREDENTIAL.split(".", 1)[0]
                        if (message.get("authorized") is not True or str(message.get("attention_id")) != attention_id
                                or str(message.get("terminal_id")) != expected_terminal):
                            raise CashSyncError("central_control_invalid_message")
                        self._last_live = time.monotonic()
                        self._authorized = True
                        self.error = ""
                        ready.set()
                    elif kind == "heartbeat" and pending_nonce and message.get("nonce") == pending_nonce:
                        self._last_live = time.monotonic()
                        pending_nonce = None
                    elif kind == "collection.blocked":
                        raise CashSyncError(message.get("code") or "station_blocked")
        except Exception as exc:
            if not stop_event.is_set():
                self.deny(exc.code if isinstance(exc, CashSyncError) else "central_control_unavailable")
        finally:
            if self._connection is connection:
                self._connection = None
            ready.set()

    def stop(self):
        self._stop.set()
        self._authorized = False
        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None


_stream = CentralControlStream()


def get_control_stream():
    return _stream
