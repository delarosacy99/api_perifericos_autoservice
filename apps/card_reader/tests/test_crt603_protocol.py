from django.test import SimpleTestCase

from ..drivers.base import CardReaderProtocolError
from ..drivers.crt603_protocol import (
    CRT603SerialConfig,
    CRT603SerialTransport,
    decode_frame,
    encode_frame,
)


class FakeSerial:
    def __init__(self, *, response: bytes, **kwargs) -> None:
        self.response = bytearray(response)
        self.kwargs = kwargs
        self.writes = []
        self.is_open = True
        self.input_reset = False

    def reset_input_buffer(self):
        self.input_reset = True

    def write(self, packet):
        self.writes.append(packet)
        return len(packet)

    def flush(self):
        return None

    def read(self, size):
        if not self.response:
            return b""
        chunk = self.response[:size]
        del self.response[:size]
        return bytes(chunk)

    def close(self):
        self.is_open = False


class CRT603ProtocolTestCase(SimpleTestCase):
    def test_frame_encoding_matches_documented_xor_format(self):
        self.assertEqual(encode_frame(0x30), bytes.fromhex("02 30 32"))
        self.assertEqual(
            encode_frame(0x31, bytes.fromhex("9060000000")),
            bytes.fromhex("07 31 90 60 00 00 00 C6"),
        )

    def test_frame_decoding_validates_bcc(self):
        command, data = decode_frame(bytes.fromhex("09 31 04 01 01 33 00 1A 05 10"))
        self.assertEqual(command, 0x31)
        self.assertEqual(data, bytes.fromhex("04010133001A05"))

        bad_frame = bytearray(encode_frame(0x30, b"\x01"))
        bad_frame[-1] ^= 0xFF
        with self.assertRaises(CardReaderProtocolError):
            decode_frame(bytes(bad_frame))

    def test_serial_transport_uses_observed_com_settings(self):
        response = encode_frame(0x03, b"C603_TEST")
        instances = []

        def factory(**kwargs):
            instance = FakeSerial(response=response, **kwargs)
            instances.append(instance)
            return instance

        transport = CRT603SerialTransport(
            CRT603SerialConfig(port="COM1", baud_rate=9600, attempts=1),
            serial_factory=factory,
        )

        received = transport.exchange(0x03)

        self.assertEqual(received, b"C603_TEST")
        self.assertEqual(instances[0].kwargs["port"], "COM1")
        self.assertEqual(instances[0].kwargs["baudrate"], 9600)
        self.assertEqual(instances[0].kwargs["bytesize"], 8)
        self.assertEqual(instances[0].kwargs["parity"], "N")
        self.assertEqual(instances[0].kwargs["stopbits"], 1)
        self.assertTrue(instances[0].input_reset)
        self.assertEqual(instances[0].writes, [bytes.fromhex("02 03 01")])
