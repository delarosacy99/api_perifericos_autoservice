from django.test import SimpleTestCase

from ..drivers.base import DoorMonitorConfigurationError, DoorMonitorConnectionError
from ..drivers.xl128 import (
    QUERY_COMMAND,
    XL128SerialConfig,
    XL128SerialDoorMonitor,
    XL128SerialTransport,
)


class FakeSerial:
    def __init__(self, responses, **kwargs):
        self.responses = list(responses)
        self.kwargs = kwargs
        self.is_open = True
        self.writes = []
        self.reset_calls = 0

    def reset_input_buffer(self):
        self.reset_calls += 1

    def write(self, payload):
        self.writes.append(payload)
        return len(payload)

    def flush(self):
        return None

    def read(self, size):
        if not self.responses:
            return b""
        return self.responses.pop(0)

    def close(self):
        self.is_open = False


class XL128DriverTestCase(SimpleTestCase):
    def make_transport(self, responses, *, attempts=2):
        instances = []

        def factory(**kwargs):
            instance = FakeSerial(responses, **kwargs)
            instances.append(instance)
            return instance

        transport = XL128SerialTransport(
            XL128SerialConfig(port="COM7", attempts=attempts),
            serial_factory=factory,
        )
        return transport, instances

    def test_query_uses_documented_read_only_command_and_9600_8n1(self):
        transport, instances = self.make_transport([bytes.fromhex("10")])

        result = transport.query()

        self.assertEqual(result, 0x10)
        self.assertEqual(instances[0].writes, [QUERY_COMMAND])
        self.assertEqual(instances[0].kwargs["port"], "COM7")
        self.assertEqual(instances[0].kwargs["baudrate"], 9600)
        self.assertEqual(instances[0].kwargs["bytesize"], 8)
        self.assertEqual(instances[0].kwargs["parity"], "N")
        self.assertEqual(instances[0].kwargs["stopbits"], 1)
        self.assertFalse(instances[0].kwargs["xonxoff"])
        self.assertFalse(instances[0].kwargs["rtscts"])
        self.assertFalse(instances[0].kwargs["dsrdtr"])

    def test_query_retries_after_timeout(self):
        transport, instances = self.make_transport([b"", bytes.fromhex("08")])

        result = transport.query()

        self.assertEqual(result, 0x08)
        self.assertEqual(instances[0].writes, [QUERY_COMMAND, QUERY_COMMAND])

    def test_query_reports_timeout_and_closes_port(self):
        transport, instances = self.make_transport([b""], attempts=1)

        with self.assertRaisesMessage(DoorMonitorConnectionError, "no respondió"):
            transport.query()

        self.assertFalse(instances[0].is_open)

    def test_driver_maps_calibrated_open_and_closed_codes(self):
        transport, _instances = self.make_transport([bytes.fromhex("01"), bytes.fromhex("00")])
        driver = XL128SerialDoorMonitor(
            XL128SerialConfig(port="COM7"),
            open_codes=(0x01,),
            closed_codes=(0x00,),
            transport=transport,
        )

        opened = driver.read_status()
        closed = driver.read_status()

        self.assertEqual(opened.state, "open")
        self.assertTrue(opened.is_open)
        self.assertEqual(opened.door_id, "front")
        self.assertEqual(opened.coverage, "front_door_only")
        self.assertEqual(opened.raw_status_hex, "01")
        self.assertTrue(opened.sdk_documented_status)
        self.assertTrue(opened.physically_calibrated_status)
        self.assertEqual(closed.state, "closed")
        self.assertFalse(closed.is_open)
        self.assertEqual(closed.raw_status_hex, "00")
        self.assertFalse(closed.sdk_documented_status)
        self.assertTrue(closed.physically_calibrated_status)

    def test_driver_preserves_unknown_response_for_physical_calibration(self):
        transport, _instances = self.make_transport([bytes.fromhex("7F")])
        driver = XL128SerialDoorMonitor(
            XL128SerialConfig(port="COM7"),
            transport=transport,
        )

        observation = driver.read_status()

        self.assertEqual(observation.state, "unknown")
        self.assertIsNone(observation.is_open)
        self.assertEqual(observation.raw_status_hex, "7F")
        self.assertFalse(observation.sdk_documented_status)
        self.assertFalse(observation.physically_calibrated_status)
        self.assertTrue(observation.monitoring_only)

    def test_driver_rejects_ambiguous_state_mapping(self):
        with self.assertRaises(DoorMonitorConfigurationError):
            XL128SerialDoorMonitor(
                XL128SerialConfig(port="COM7"),
                open_codes=(0x10,),
                closed_codes=(0x10,),
            )
