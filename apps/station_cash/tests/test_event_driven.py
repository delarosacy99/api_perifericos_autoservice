import json
import threading
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from websockets.sync.server import serve

from apps.station_cash.control import CentralControlStream, control_url
from apps.station_cash.runtime import CashSyncWorker, wake_sync_worker


@override_settings(DEBUG=True, CENTRAL_AGENT_CREDENTIAL="38.secret-long-enough-for-tests",
                   CENTRAL_TIMEOUT_SECONDS=1, CENTRAL_CONTROL_WS_URL="")
class CentralControlTransportTests(SimpleTestCase):
    def start_server(self, handler):
        server = serve(handler, "127.0.0.1", 0, ping_interval=None, close_timeout=.2)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        port = server.socket.getsockname()[1]
        self.enable_setting = override_settings(CENTRAL_API_BASE_URL=f"http://127.0.0.1:{port}/api/organizacion/autoservice/")
        self.enable_setting.enable()
        self.addCleanup(self.enable_setting.disable)
        stream = CentralControlStream()
        stream.heartbeat_seconds = .03
        stream.silence_seconds = .2
        self.addCleanup(stream.stop)
        return stream

    def handler(self, *, block=False, reply_heartbeat=True, wrong_attention=False):
        self.heartbeat_seen = threading.Event()
        self.failed = threading.Event()
        def run(connection):
            auth = json.loads(connection.recv(timeout=1))
            self.assertEqual(auth["type"], "authenticate")
            connection.send(json.dumps({"type": "collection.authorized", "authorized": True,
                                         "terminal_id": 38,
                                         "attention_id": str(uuid4()) if wrong_attention else auth["attention_id"]}))
            try:
                for raw in connection:
                    message = json.loads(raw)
                    if message["type"] == "heartbeat":
                        self.heartbeat_seen.set()
                        if block:
                            connection.send(json.dumps({"type": "collection.blocked", "code": "station_blocked"}))
                            return
                        if reply_heartbeat:
                            connection.send(json.dumps(message))
            finally:
                self.failed.set()
        return run

    def test_live_heartbeat_uses_websocket_and_stop_closes_it(self):
        stream = self.start_server(self.handler())
        attention_id = uuid4()
        stream.authorize(attention_id)
        self.assertTrue(stream.is_authorized(attention_id))
        self.assertTrue(self.heartbeat_seen.wait(2))
        stream.stop()
        self.assertFalse(stream.is_authorized(attention_id))
        self.assertTrue(self.failed.wait(2))

    def test_pushed_block_physically_inhibits_without_another_banknote(self):
        stream = self.start_server(self.handler(block=True))
        with patch("apps.cash_acceptor.runtime.get_cash_acceptor_poller") as poller:
            inhibited = threading.Event()
            poller.return_value.fail_closed.side_effect = inhibited.set
            attention_id = uuid4()
            stream.authorize(attention_id)
            self.assertTrue(inhibited.wait(2))
            self.assertFalse(stream.is_authorized(attention_id))
            self.assertEqual(stream.error, "station_blocked")

    def test_missing_application_heartbeat_inhibits_even_if_socket_remains_open(self):
        stream = self.start_server(self.handler(reply_heartbeat=False))
        with patch("apps.cash_acceptor.runtime.get_cash_acceptor_poller") as poller:
            inhibited = threading.Event()
            poller.return_value.fail_closed.side_effect = inhibited.set
            attention_id = uuid4()
            stream.authorize(attention_id)
            self.assertTrue(inhibited.wait(3))
            self.assertFalse(stream.is_authorized(attention_id))
            self.assertEqual(stream.error, "central_control_timeout")

    def test_wrong_attention_never_authorizes_acceptance(self):
        from apps.station_cash.central import CashSyncError
        stream = self.start_server(self.handler(wrong_attention=True))
        with patch("apps.cash_acceptor.runtime.get_cash_acceptor_poller"):
            with self.assertRaises(CashSyncError):
                stream.authorize(uuid4())

    def test_control_url_is_derived_and_rejects_credentials_or_insecure_public_hosts(self):
        with override_settings(CENTRAL_API_BASE_URL="https://autoservicio.example.test/api/organizacion/autoservice/"):
            self.assertEqual(control_url(), "wss://autoservicio.example.test/ws/autoservice/agent/control/")
        from apps.station_cash.central import CashSyncError
        for url in ("ws://public.example.test/ws/", "wss://user:secret@example.test/ws/", "wss://example.test/ws/?secret=key"):
            with self.subTest(url=url), override_settings(CENTRAL_CONTROL_WS_URL=url):
                with self.assertRaises(CashSyncError):
                    control_url()


class EventDrivenWorkerTests(SimpleTestCase):
    def test_idle_worker_sleeps_until_an_event_without_periodic_database_cycles(self):
        first = threading.Event()
        second = threading.Event()
        def cycle():
            if first.is_set():
                second.set()
            first.set()
        worker = CashSyncWorker()
        with patch("apps.station_cash.services.worker_cycle", side_effect=cycle) as cycle_mock:
            worker.thread.start()
            try:
                self.assertTrue(first.wait(1))
                self.assertFalse(second.wait(1.1))
                self.assertEqual(cycle_mock.call_count, 1)
                wake_sync_worker()
                self.assertTrue(second.wait(1))
            finally:
                worker.stop_event.set()
                wake_sync_worker()
                worker.thread.join(2)

    def test_retry_deadline_wakes_worker_without_periodic_polling(self):
        retry = threading.Event()
        calls = []
        def cycle():
            calls.append(1)
            if len(calls) == 1:
                return timezone.now() + timedelta(seconds=.03)
            retry.set()
        worker = CashSyncWorker()
        with patch("apps.station_cash.services.worker_cycle", side_effect=cycle):
            worker.thread.start()
            try:
                self.assertTrue(retry.wait(1))
                self.assertEqual(len(calls), 2)
            finally:
                worker.stop_event.set()
                wake_sync_worker()
                worker.thread.join(2)
