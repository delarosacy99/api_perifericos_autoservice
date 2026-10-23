from django.test import SimpleTestCase

from ..drivers.base import CashAcceptorConnectionError
from ..drivers.essp import SSPTransportError
from ..drivers.nv200 import (
    CMD_DISABLE,
    CMD_ENABLE,
    CMD_EVENT_ACK,
    CMD_GET_DATASET_VERSION,
    CMD_GET_FIRMWARE_VERSION,
    CMD_GET_SERIAL_NUMBER,
    CMD_HOST_PROTOCOL_VERSION,
    CMD_POLL_WITH_ACK,
    CMD_SET_INHIBITS,
    CMD_SETUP_REQUEST,
    NV200Config,
    NV200ESSPDriver,
    parse_poll_response,
    parse_setup_response,
)


def make_setup_response() -> bytes:
    channels = (("USD", 100), ("USD", 500), ("USD", 2_000))
    data = bytearray()
    data.append(0x00)
    data.extend(b"1234")
    data.extend(b"USD")
    data.extend(b"\x00\x00\x00")
    data.append(len(channels))
    data.extend(b"\x00" * len(channels))
    data.extend(b"\x00" * len(channels))
    data.extend(b"\x00\x00\x01")
    data.append(8)
    for currency, _value in channels:
        data.extend(currency.encode("ascii"))
    for _currency, value in channels:
        data.extend(value.to_bytes(4, "little"))
    return b"\xf0" + bytes(data)


class FakeSSPClient:
    def __init__(self) -> None:
        self.calls = []
        self.opened = False
        self.negotiated = False
        self.fail_poll = False
        self.fail_ack = False
        self.poll_response = bytes.fromhex("F0 CC EE 03 EB")

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def negotiate_keys(self):
        self.negotiated = True
        return 123

    def command(self, command, parameters=b"", *, require_ok=True, encrypted=None):
        self.calls.append((command, parameters, require_ok, encrypted))
        if command == CMD_HOST_PROTOCOL_VERSION:
            return b"\xf0" if parameters[0] <= 8 else b"\xf8"
        if command == CMD_SETUP_REQUEST:
            return make_setup_response()
        if command == CMD_GET_SERIAL_NUMBER:
            return b"\xf0" + (1_873_452).to_bytes(4, "big")
        if command == CMD_GET_FIRMWARE_VERSION:
            return b"\xf0NV02004141498000"
        if command == CMD_GET_DATASET_VERSION:
            return b"\xf0USD01610"
        if command == CMD_POLL_WITH_ACK:
            if self.fail_poll:
                raise SSPTransportError("COM9 fue desconectado")
            return self.poll_response
        if command == CMD_EVENT_ACK and self.fail_ack:
            self.fail_ack = False
            raise SSPTransportError("COM9 fue desconectado antes del ACK")
        return b"\xf0"


class NV200DriverTestCase(SimpleTestCase):
    def setUp(self):
        self.client = FakeSSPClient()
        self.driver = NV200ESSPDriver(
            NV200Config(
                port="COM9",
                minimum_protocol_version=6,
                maximum_protocol_version=9,
            ),
            client=self.client,
        )

    def test_setup_response_uses_expanded_currency_and_values(self):
        setup = parse_setup_response(make_setup_response())

        self.assertEqual(setup.unit_type, 0)
        self.assertEqual(setup.protocol_version, 8)
        self.assertEqual(setup.firmware_version, "12.34")
        self.assertEqual(
            [(item.channel, item.currency, item.denomination_minor) for item in setup.channels],
            [(1, "USD", 100), (2, "USD", 500), (3, "USD", 2_000)],
        )

    def test_connect_negotiates_metadata_and_leaves_validator_disabled(self):
        snapshot = self.driver.connect()

        self.assertTrue(self.client.opened)
        self.assertTrue(self.client.negotiated)
        self.assertTrue(snapshot.connected)
        self.assertFalse(snapshot.accepting)
        self.assertEqual(snapshot.serial_number, "1873452")
        self.assertEqual(snapshot.dataset_version, "USD01610")
        self.assertEqual(snapshot.firmware_version, "NV02004141498000")
        self.assertEqual(self.driver._protocol_version, 8)
        self.assertIn((CMD_DISABLE, b"", True, None), self.client.calls)

    def test_enable_builds_three_byte_channel_mask(self):
        self.driver.connect()

        enabled = self.driver.enable_currency("USD", {100, 2_000})

        self.assertEqual(enabled, (1, 3))
        self.assertIn((CMD_SET_INHIBITS, b"\x05\x00\x00", True, None), self.client.calls)
        self.assertIn((CMD_ENABLE, b"", True, None), self.client.calls)
        self.assertTrue(self.driver.snapshot().accepting)

    def test_poll_credit_is_stable_until_event_ack(self):
        self.driver.connect()
        self.driver.enable_currency("USD")

        first = self.driver.poll_once()
        second = self.driver.poll_once()

        self.assertEqual(first, second)
        self.assertTrue(first.requires_ack)
        self.assertEqual(len(first.note_credits), 1)
        credit = first.note_credits[0]
        self.assertEqual(credit.channel, 3)
        self.assertEqual(credit.currency, "USD")
        self.assertEqual(credit.denomination_minor, 2_000)
        self.assertEqual(
            sum(call[0] == CMD_POLL_WITH_ACK for call in self.client.calls),
            1,
        )

        self.driver.acknowledge_events(first.batch_id)
        self.assertIn((CMD_EVENT_ACK, b"", True, None), self.client.calls)
        self.driver.poll_once()
        self.assertEqual(
            sum(call[0] == CMD_POLL_WITH_ACK for call in self.client.calls),
            2,
        )

    def test_poll_parser_keeps_event_order(self):
        events = parse_poll_response(bytes.fromhex("F0 F1 EF 02 CC EE 02 EB"))

        self.assertEqual(
            [event.name for event in events],
            ["SLAVE_RESET", "NOTE_READ", "NOTE_STACKING", "NOTE_CREDIT", "NOTE_STACKED"],
        )
        self.assertEqual(events[1].data, b"\x02")
        self.assertEqual(events[3].data, b"\x02")

    def test_poll_transport_failure_marks_driver_disconnected(self):
        self.driver.connect()
        self.driver.enable_currency("USD")
        self.client.fail_poll = True

        with self.assertRaises(CashAcceptorConnectionError):
            self.driver.poll_once()

        snapshot = self.driver.snapshot()
        self.assertFalse(snapshot.connected)
        self.assertFalse(snapshot.accepting)
        self.assertFalse(self.client.opened)

    def test_cashbox_removal_is_exposed_once_and_disables_acceptance(self):
        self.driver.connect()
        self.driver.enable_currency("USD")
        self.client.poll_response = bytes.fromhex("F0 E3")

        first = self.driver.poll_once()
        second = self.driver.poll_once(allow_disabled=True)

        self.assertEqual(first.state_changes, ("CASHBOX_REMOVED",))
        self.assertEqual(second.state_changes, ())
        snapshot = self.driver.snapshot()
        self.assertFalse(snapshot.cashbox_present)
        self.assertFalse(snapshot.accepting)
        self.assertEqual(snapshot.alerts, ("cashbox_removed",))
        self.assertFalse(any(channel.enabled for channel in snapshot.channels))

    def test_cashbox_replacement_clears_alert_and_allows_enable(self):
        self.driver.connect()
        self.driver.enable_currency("USD")
        self.client.poll_response = bytes.fromhex("F0 E3")
        self.driver.poll_once()
        self.client.poll_response = bytes.fromhex("F0 E4")

        replaced = self.driver.poll_once(allow_disabled=True)
        enabled = self.driver.enable_currency("USD")

        self.assertEqual(replaced.state_changes, ("CASHBOX_REPLACED",))
        snapshot = self.driver.snapshot()
        self.assertTrue(snapshot.cashbox_present)
        self.assertEqual(snapshot.alerts, ())
        self.assertTrue(snapshot.accepting)
        self.assertEqual(enabled, (1, 2, 3))

    def test_pending_credit_keeps_same_id_across_ack_reconnection(self):
        self.driver.connect()
        self.driver.enable_currency("USD")
        first_batch = self.driver.poll_once()
        first_event_id = first_batch.note_credits[0].event_id
        self.client.fail_ack = True

        with self.assertRaises(CashAcceptorConnectionError):
            self.driver.acknowledge_events(first_batch.batch_id)

        self.assertFalse(self.driver.snapshot().connected)

        self.driver.connect()
        self.driver.enable_currency("USD")
        recovered_batch = self.driver.poll_once()

        self.assertEqual(recovered_batch.batch_id, first_batch.batch_id)
        self.assertEqual(recovered_batch.note_credits[0].event_id, first_event_id)
        self.driver.acknowledge_events(recovered_batch.batch_id)
        self.assertIsNone(self.driver._pending_batch)
