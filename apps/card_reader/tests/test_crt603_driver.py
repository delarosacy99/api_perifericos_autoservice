import uuid
from unittest.mock import Mock

from django.test import SimpleTestCase

from ..drivers.base import CardReaderNoCard, CardReaderProtocolError
from ..drivers.crt603 import (
    CREDENTIAL_FILE_SIZE,
    CRT603SerialDriver,
)
from ..drivers.crt603_protocol import CRT603CommandError, CRT603SerialConfig
from ..drivers.desfire import (
    APDU_ADDITIONAL_FRAME,
    APDU_GET_APPLICATION_IDS,
    APDU_GET_FREE_MEMORY,
    APDU_GET_VERSION,
)
from ..drivers.desfire_security import aes_cmac_with_iv, wrap_native_apdu


class FakeCRT603Transport:
    def __init__(self, exchanges) -> None:
        self.exchanges = list(exchanges)
        self.calls = []
        self.is_open = False

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def exchange(self, command, data=b""):
        self.calls.append((command, data))
        expected_command, expected_data, result = self.exchanges.pop(0)
        if (command, data) != (expected_command, expected_data):
            raise AssertionError(
                f"Se esperaba {(expected_command, expected_data)!r} "
                f"y se recibió {(command, data)!r}"
            )
        if isinstance(result, Exception):
            raise result
        return result


def successful_exchanges():
    return [
        (0x03, b"", b"C603_XD1_A_TEST"),
        (0x70, b"\x00", b""),
        (0x20, b"\x00", bytes.fromhex("04C94E2A771890440320")),
        (0x30, b"", bytes.fromhex("067577810280")),
        (0x31, APDU_GET_VERSION, bytes.fromhex("04010133001A0591AF")),
        (0x31, APDU_ADDITIONAL_FRAME, bytes.fromhex("04010103001A0591AF")),
        (
            0x31,
            APDU_ADDITIONAL_FRAME,
            bytes.fromhex("04C94E2A771890207235303023239100"),
        ),
        (0x31, APDU_GET_APPLICATION_IDS, bytes.fromhex("9100")),
        (0x31, APDU_GET_FREE_MEMORY, bytes.fromhex("0020009100")),
    ]


class CRT603DriverTestCase(SimpleTestCase):
    def make_driver(self, exchanges):
        transport = FakeCRT603Transport(exchanges)
        driver = CRT603SerialDriver(
            CRT603SerialConfig(port="COM1", baud_rate=9600),
            credential_application_id="D3E001",
            transport=transport,
        )
        return driver, transport

    def test_inspection_reproduces_observed_desfire_ev3_card(self):
        driver, transport = self.make_driver(successful_exchanges())

        inspection = driver.inspect_card()

        self.assertEqual(inspection.card_type, "mifare_desfire_ev3")
        self.assertEqual(inspection.uid, "04C94E2A771890")
        self.assertEqual(inspection.rf_uid, "04C94E2A771890")
        self.assertEqual(inspection.atr, "067577810280")
        self.assertEqual(inspection.capacity_bytes, 8192)
        self.assertEqual(inspection.free_memory_bytes, 8192)
        self.assertEqual(inspection.application_ids, ())
        self.assertEqual(inspection.credential_state, "blank")
        self.assertEqual(inspection.production_week, 23)
        self.assertEqual(inspection.production_year, 2023)
        self.assertEqual(transport.exchanges, [])

    def test_seek_failure_is_reported_as_no_card(self):
        failure = CRT603CommandError(
            command=0x20,
            reply_command=0xDF,
            error_code=None,
        )
        driver, _transport = self.make_driver(
            [
                (0x03, b"", b"C603_XD1_A_TEST"),
                (0x70, b"\x00", b""),
                (0x20, b"\x00", failure),
            ]
        )

        with self.assertRaises(CardReaderNoCard):
            driver.inspect_card()

        self.assertFalse(driver.snapshot().card_present)

    def test_create_application_uses_expected_aid_and_two_aes_keys(self):
        driver, _transport = self.make_driver([])
        driver._send_apdu = Mock(return_value=bytes.fromhex("9100"))

        driver._create_credential_application(bytes.fromhex("D3E001"))

        driver._send_apdu.assert_called_once_with(
            wrap_native_apdu(0xCA, bytes.fromhex("D3E0010982"))
        )

    def test_read_credential_validates_response_cmac(self):
        driver, _transport = self.make_driver([])
        session_key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
        credential_id = uuid.UUID("4dc84d82-93ef-4d26-a1d1-e399b686a5e5")
        credential_payload = b"\x01" + credential_id.bytes
        read_parameters = b"\x00" + bytes(3) + CREDENTIAL_FILE_SIZE.to_bytes(3, "little")
        command_cmac = aes_cmac_with_iv(
            session_key,
            bytes(16),
            b"\xbd" + read_parameters,
        )
        response_cmac = aes_cmac_with_iv(
            session_key,
            command_cmac,
            credential_payload + b"\x00",
        )[:8]
        driver._send_apdu = Mock(
            return_value=credential_payload + response_cmac + bytes.fromhex("9100")
        )

        version, parsed_id = driver._read_credential(session_key=session_key)

        self.assertEqual(version, 1)
        self.assertEqual(parsed_id, str(credential_id))

    def test_read_credential_rejects_invalid_cmac(self):
        driver, _transport = self.make_driver([])
        driver._send_apdu = Mock(
            return_value=b"\x01" + bytes(16) + bytes(8) + bytes.fromhex("9100")
        )

        with self.assertRaisesMessage(CardReaderProtocolError, "CMAC"):
            driver._read_credential(session_key=bytes(16))
