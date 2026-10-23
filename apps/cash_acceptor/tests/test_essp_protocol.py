from django.test import SimpleTestCase

from ..drivers.essp import (
    SerialSSPTransport,
    SSPCipher,
    SSPPacketError,
    SSPSerialConfig,
    SSPStreamDecoder,
    crc16_ssp,
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

    def read(self, _size):
        if not self.response:
            return b""
        return bytes((self.response.pop(0),))

    def close(self):
        self.is_open = False


class ESSPProtocolTestCase(SimpleTestCase):
    def test_frames_match_official_nv200_examples(self):
        self.assertEqual(
            encode_frame(address=0, sequence=0x80, data=b"\x07"),
            bytes.fromhex("7F 80 01 07 12 02"),
        )
        self.assertEqual(
            encode_frame(address=0, sequence=0x80, data=b"\x06\x08"),
            bytes.fromhex("7F 80 02 06 08 03 94"),
        )
        self.assertEqual(
            encode_frame(address=0, sequence=0x80, data=b"\x02\x07\x00"),
            bytes.fromhex("7F 80 03 02 07 00 2B B6"),
        )

    def test_stream_decoder_removes_stuffing_across_fragmented_reads(self):
        encoded = encode_frame(
            address=0,
            sequence=0x80,
            data=bytes((0x20, 0x7F, 0x01)),
        )
        self.assertIn(bytes((0x7F, 0x7F)), encoded[1:])

        decoder = SSPStreamDecoder()
        frames = []
        for value in encoded:
            frames.extend(decoder.feed(bytes((value,))))

        self.assertEqual(len(frames), 1)
        address, sequence, data = decode_frame(frames[0])
        self.assertEqual(address, 0)
        self.assertEqual(sequence, 0x80)
        self.assertEqual(data, bytes((0x20, 0x7F, 0x01)))

    def test_bad_transport_crc_is_rejected(self):
        frame = bytearray(bytes.fromhex("7F 80 01 07 12 02"))
        frame[-1] ^= 0xFF

        with self.assertRaises(SSPPacketError):
            decode_frame(bytes(frame))

    def test_cipher_round_trip_and_counter_validation(self):
        cipher = SSPCipher(
            fixed_key=0x0123456701234567,
            variable_key=0x1122334455667788,
        )
        data = bytes.fromhex("F0 EE 04 EB")
        encrypted = cipher.encrypt(data, counter=7, padding=b"\xa5" * 5)

        self.assertEqual(cipher.decrypt(encrypted, expected_counter=7), data)
        with self.assertRaises(SSPPacketError):
            cipher.decrypt(encrypted, expected_counter=8)

    def test_crc_is_stable_for_official_poll_body(self):
        self.assertEqual(crc16_ssp(bytes.fromhex("80 01 07")), 0x0212)

    def test_serial_transport_uses_8n2_and_returns_valid_frame(self):
        reply = encode_frame(address=0, sequence=0x80, data=b"\xf0")
        instances = []

        def factory(**kwargs):
            instance = FakeSerial(response=reply, **kwargs)
            instances.append(instance)
            return instance

        transport = SerialSSPTransport(
            SSPSerialConfig(port="COM7", timeout_seconds=0.01, attempts=1),
            serial_factory=factory,
        )
        transport.open()
        command = encode_frame(address=0, sequence=0x80, data=b"\x07")

        received = transport.exchange(command, expected_address_sequence=0x80)

        self.assertEqual(decode_frame(received)[2], b"\xf0")
        self.assertEqual(instances[0].kwargs["port"], "COM7")
        self.assertEqual(instances[0].kwargs["baudrate"], 9600)
        self.assertEqual(instances[0].kwargs["bytesize"], 8)
        self.assertEqual(instances[0].kwargs["parity"], "N")
        self.assertEqual(instances[0].kwargs["stopbits"], 2)
        self.assertTrue(instances[0].input_reset)
        self.assertEqual(instances[0].writes, [command])
