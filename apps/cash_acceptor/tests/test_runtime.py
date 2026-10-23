from unittest.mock import patch

from django.test import TestCase, override_settings

from ..drivers.base import CashAcceptorSnapshot, CashChannel, NoteCreditEvent
from ..drivers.nv200 import NV200PollBatch, NV200PollEvent
from ..models import AcceptedBanknote, CashCollectionSession
from ..runtime import CashAcceptorPoller
from ..services import CashboxRemoved, connect_and_enable_collection_hardware, stop_collection


class FakePollingDriver:
    supports_polling = True

    def __init__(self):
        self.acknowledged = []
        self.disabled = False
        self.channel = CashChannel(
            channel=1,
            currency="USD",
            denomination_minor=2_000,
            enabled=True,
        )

    def snapshot(self):
        return CashAcceptorSnapshot(
            driver="fake",
            model="NV200",
            serial_number="123",
            connected=True,
            accepting=not self.disabled,
            dataset_version="USD-TEST",
            firmware_version="TEST",
            channels=(self.channel,),
        )

    def poll_once(self):
        return NV200PollBatch(
            batch_id="batch-001",
            events=(NV200PollEvent(code=0xEE, name="NOTE_CREDIT", data=b"\x01"),),
            note_credits=(
                NoteCreditEvent(
                    event_id="nv200-credit-001",
                    channel=1,
                    currency="USD",
                    denomination_minor=2_000,
                    raw_event={"event": "NOTE_CREDIT", "channel": 1},
                ),
            ),
            requires_ack=True,
        )

    def acknowledge_events(self, batch_id):
        # ACK must happen only after the note is durable in the local database.
        assert AcceptedBanknote.objects.filter(device_event_id="nv200-credit-001").exists()
        self.acknowledged.append(batch_id)

    def disable(self):
        self.disabled = True


@override_settings(
    CASH_EXPECTED_DATASET_CURRENCY="USD",
    CASH_ACCEPT_ALL_DATASET_DENOMINATIONS=True,
)
class CashAcceptorPollerTestCase(TestCase):
    def test_credit_is_saved_before_event_ack(self):
        collection = CashCollectionSession.objects.create(
            idempotency_key="poller-test",
            remote_session_id="remote-poller-test",
            currency="USD",
            status=CashCollectionSession.Status.ACCEPTING,
        )
        driver = FakePollingDriver()
        poller = CashAcceptorPoller(
            driver_factory=lambda: driver,
            interval_seconds=0.01,
        )

        self.assertTrue(poller.process_once())

        collection.refresh_from_db()
        self.assertEqual(collection.accepted_amount_minor, 2_000)
        self.assertEqual(collection.accepted_banknotes.count(), 1)
        self.assertEqual(driver.acknowledged, ["batch-001"])

    def test_stop_disables_then_drains_last_hardware_credit(self):
        collection = CashCollectionSession.objects.create(
            idempotency_key="stop-drain-test",
            remote_session_id="remote-stop-drain",
            currency="USD",
            status=CashCollectionSession.Status.ACCEPTING,
        )
        driver = StopDrainDriver()

        with patch("apps.cash_acceptor.services.get_cash_acceptor", return_value=driver):
            stopped = stop_collection(collection.id, reason="operator_requested")

        self.assertTrue(driver.disabled)
        self.assertEqual(driver.calls[:2], ["disable", "poll"])
        self.assertEqual(stopped.status, CashCollectionSession.Status.STOPPED)
        self.assertEqual(stopped.accepted_amount_minor, 2_000)
        self.assertEqual(driver.acknowledged, ["stop-batch-001"])

    def test_poller_reconnects_and_reenables_active_collection(self):
        collection = CashCollectionSession.objects.create(
            idempotency_key="poller-recovery-test",
            remote_session_id="remote-poller-recovery",
            currency="USD",
            status=CashCollectionSession.Status.ACCEPTING,
        )
        driver = RecoveringPollingDriver()
        poller = CashAcceptorPoller(
            driver_factory=lambda: driver,
            interval_seconds=0.01,
        )

        self.assertTrue(poller.process_once())

        collection.refresh_from_db()
        self.assertEqual(driver.connect_count, 1)
        self.assertEqual(driver.enabled_currency, "USD")
        self.assertEqual(collection.accepted_amount_minor, 2_000)
        self.assertEqual(collection.accepted_banknotes.count(), 1)
        self.assertEqual(driver.acknowledged, ["batch-001"])

    def test_cashbox_removal_stops_collection_and_logs_transition_once(self):
        collection = CashCollectionSession.objects.create(
            idempotency_key="cashbox-removal-test",
            remote_session_id="remote-cashbox-removal",
            currency="USD",
            status=CashCollectionSession.Status.ACCEPTING,
        )
        driver = CashboxRemovedPollingDriver()
        poller = CashAcceptorPoller(
            driver_factory=lambda: driver,
            interval_seconds=0.01,
        )

        with (
            patch("apps.cash_acceptor.services.get_cash_acceptor", return_value=driver),
            self.assertLogs("apps.cash_acceptor.runtime", level="ERROR") as captured,
        ):
            self.assertTrue(poller.process_once())
            self.assertFalse(poller.process_once())

        collection.refresh_from_db()
        self.assertEqual(collection.status, CashCollectionSession.Status.STOPPED)
        self.assertEqual(collection.stop_reason, CashCollectionSession.StopReason.DEVICE_ERROR)
        self.assertTrue(driver.disabled)
        self.assertEqual(
            sum("CASHBOX_REMOVED" in message for message in captured.output),
            1,
        )

    def test_created_collection_cannot_enable_with_cashbox_removed(self):
        collection = CashCollectionSession.objects.create(
            idempotency_key="cashbox-preflight-test",
            remote_session_id="remote-cashbox-preflight",
            currency="USD",
        )
        driver = MissingCashboxDriver()

        with self.assertRaises(CashboxRemoved):
            connect_and_enable_collection_hardware(collection, driver=driver)

        self.assertTrue(driver.disabled)
        self.assertFalse(driver.enable_called)


class StopDrainDriver(FakePollingDriver):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.poll_count = 0

    def disable(self):
        self.calls.append("disable")
        self.disabled = True

    def poll_once(self, *, allow_disabled=False):
        self.calls.append("poll")
        assert allow_disabled
        self.poll_count += 1
        if self.poll_count == 1:
            batch = super().poll_once()
            return NV200PollBatch(
                batch_id="stop-batch-001",
                events=batch.events,
                note_credits=batch.note_credits,
                requires_ack=True,
            )
        return NV200PollBatch(
            batch_id="stop-batch-empty",
            events=(NV200PollEvent(code=0xE8, name="DISABLED", data=b""),),
            note_credits=(),
            requires_ack=False,
        )


class CashboxRemovedPollingDriver(FakePollingDriver):
    def __init__(self):
        super().__init__()
        self.poll_count = 0

    def poll_once(self, *, allow_disabled=False):
        self.poll_count += 1
        if self.poll_count == 1:
            self.disabled = True
            return NV200PollBatch(
                batch_id="cashbox-removed-batch",
                events=(NV200PollEvent(code=0xE3, name="CASHBOX_REMOVED", data=b""),),
                note_credits=(),
                requires_ack=False,
                state_changes=("CASHBOX_REMOVED",),
            )
        assert allow_disabled
        return NV200PollBatch(
            batch_id="cashbox-drain-empty",
            events=(NV200PollEvent(code=0xE3, name="CASHBOX_REMOVED", data=b""),),
            note_credits=(),
            requires_ack=False,
        )


class MissingCashboxDriver(FakePollingDriver):
    def __init__(self):
        super().__init__()
        self.disabled = True
        self.enable_called = False

    def snapshot(self):
        snapshot = super().snapshot()
        return CashAcceptorSnapshot(
            driver=snapshot.driver,
            model=snapshot.model,
            serial_number=snapshot.serial_number,
            connected=True,
            accepting=False,
            dataset_version=snapshot.dataset_version,
            firmware_version=snapshot.firmware_version,
            channels=(self.channel,),
            cashbox_present=False,
            alerts=("cashbox_removed",),
        )

    def connect(self):
        return self.snapshot()

    def poll_once(self, *, allow_disabled=False):
        assert allow_disabled
        return NV200PollBatch(
            batch_id="cashbox-preflight-removed",
            events=(NV200PollEvent(code=0xE3, name="CASHBOX_REMOVED", data=b""),),
            note_credits=(),
            requires_ack=False,
        )

    def enable_currency(self, currency, denominations_minor=None):
        self.enable_called = True
        return (1,)


class RecoveringPollingDriver(FakePollingDriver):
    def __init__(self):
        super().__init__()
        self.connected = False
        self.disabled = True
        self.connect_count = 0
        self.enabled_currency = None

    def snapshot(self):
        snapshot = super().snapshot()
        return CashAcceptorSnapshot(
            driver=snapshot.driver,
            model=snapshot.model,
            serial_number=snapshot.serial_number,
            connected=self.connected,
            accepting=self.connected and not self.disabled,
            dataset_version=snapshot.dataset_version,
            firmware_version=snapshot.firmware_version,
            channels=(self.channel,),
        )

    def connect(self):
        self.connect_count += 1
        self.connected = True
        return self.snapshot()

    def enable_currency(self, currency, denominations_minor=None):
        self.enabled_currency = currency
        self.disabled = False
        return (self.channel.channel,)
