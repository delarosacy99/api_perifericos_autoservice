import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from ..drivers.base import CashAcceptorProtocolError, CashAcceptorConnectionError
from ..drivers.journal import HardwareJournal
from ..drivers.nv200 import CMD_DISABLE, NV200Config, NV200ESSPDriver
from .test_nv200_driver import FakeSSPClient


class HardwareJournalTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "nv200.json")

    def driver(self):
        return NV200ESSPDriver(
            NV200Config(port="COM9", journal_path=self.path), client=FakeSSPClient()
        )

    def test_pending_credit_keeps_same_identity_after_process_restart(self):
        first = self.driver()
        first.connect()
        first.enable_currency("USD")
        batch = first.poll_once()
        second = self.driver()
        second.connect()
        recovered = second.poll_once(allow_disabled=True)
        self.assertEqual(batch.batch_id, recovered.batch_id)
        self.assertEqual(batch.note_credits[0].event_id, recovered.note_credits[0].event_id)

    def test_uncertain_hardware_ack_is_never_credited_automatically_twice(self):
        driver = self.driver()
        driver.connect()
        driver.enable_currency("USD")
        driver.poll_once()
        journal = HardwareJournal(self.path)
        data = journal.read()
        data["stage"] = "ack_intent"
        journal.write(data)
        restarted = self.driver()
        with self.assertRaisesMessage(CashAcceptorProtocolError, "ACK NV200 incierto"):
            restarted.connect()
        self.assertTrue(any(command[0] == CMD_DISABLE for command in restarted._client.calls))

    def test_successful_ack_does_not_replay_batch(self):
        driver = self.driver()
        driver.connect()
        driver.enable_currency("USD")
        batch = driver.poll_once()
        driver.acknowledge_events(batch.batch_id)
        self.assertEqual(HardwareJournal(self.path).read()["stage"], "acked")
        self.assertIsNone(self.driver()._pending_batch)

    def test_corrupt_journal_fails_closed(self):
        Path(self.path).write_text("not json")
        restarted = self.driver()
        with self.assertRaises(CashAcceptorProtocolError):
            restarted.connect()
        self.assertTrue(any(command[0] == CMD_DISABLE for command in restarted._client.calls))

    def test_ack_timeout_blocks_retry_in_same_process(self):
        driver = self.driver()
        driver.connect()
        driver.enable_currency("USD")
        batch = driver.poll_once()
        driver._client.fail_ack = True
        with self.assertRaises(CashAcceptorConnectionError):
            driver.acknowledge_events(batch.batch_id)
        with self.assertRaisesMessage(CashAcceptorProtocolError, "ACK NV200 incierto"):
            driver.connect()
        with self.assertRaises(CashAcceptorProtocolError):
            driver.acknowledge_events(batch.batch_id)

    def test_invalid_journal_note_disables_before_refusing_recovery(self):
        HardwareJournal(self.path).write({"stage": "pending", "serial_number": "123", "batch_id": "x", "events": [], "notes": [{}]})
        driver = self.driver()
        with self.assertRaises(CashAcceptorProtocolError):
            driver.connect()
        self.assertTrue(any(command[0] == CMD_DISABLE for command in driver._client.calls))

    def test_disconnect_keeps_pending_identity_in_same_process(self):
        driver = self.driver()
        driver.connect()
        driver.enable_currency("USD")
        batch = driver.poll_once()
        driver.disconnect()
        driver.connect()
        self.assertEqual(driver.poll_once(allow_disabled=True).batch_id, batch.batch_id)
