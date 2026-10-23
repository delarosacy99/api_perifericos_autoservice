from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from ..drivers.base import PrinterMonitorConnectionError
from ..drivers.ceprintlib import CustomK80CePrintLibConfig, CustomK80CePrintLibDriver
from ..drivers.status import NEAR_PAPER_END


class FakeCePrintLibClient:
    def __init__(self, *, status=0):
        self.status = status
        self.context = 0x12345678
        self.init_error = 0
        self.init_system_error = 0
        self.open_error = 0
        self.open_system_error = 0
        self.status_error = 0
        self.status_system_error = 0
        self.deinit_error = 0
        self.initialize_calls = 0
        self.open_calls = []
        self.status_calls = 0
        self.deinitialize_calls = []

    def initialize_usb(self):
        self.initialize_calls += 1
        return self.init_error, self.init_system_error, self.context

    def open_usb_device(self, context, printer_name):
        self.open_calls.append((context, printer_name))
        return self.open_error, self.open_system_error

    def get_status(self, context):
        self.status_calls += 1
        return self.status_error, self.status_system_error, self.status

    def deinitialize_usb(self, context):
        self.deinitialize_calls.append(context)
        return self.deinit_error


class CustomK80CePrintLibDriverTestCase(SimpleTestCase):
    def make_driver(self, client, **overrides):
        values = {
            "printer_name": "CUSTOM K80",
            "dll_path": "vendor/custom_k80/x64/CePrnLib.dll",
            "mapping_physically_validated": False,
        }
        values.update(overrides)
        return CustomK80CePrintLibDriver(
            CustomK80CePrintLibConfig(**values),
            client=client,
        )

    def test_bundled_x64_library_exists_and_is_a_windows_pe(self):
        dll_path = Path(settings.BASE_DIR) / "vendor/custom_k80/x64/CePrnLib.dll"

        self.assertTrue(dll_path.is_file())
        self.assertEqual(dll_path.read_bytes()[:2], b"MZ")

    def test_initializes_opens_by_queue_name_and_reuses_context(self):
        client = FakeCePrintLibClient()
        driver = self.make_driver(client)

        first = driver.read_status()
        second = driver.read_status()

        self.assertEqual(client.initialize_calls, 1)
        self.assertEqual(client.open_calls, [(client.context, "CUSTOM K80")])
        self.assertEqual(client.status_calls, 2)
        self.assertEqual(first.driver, "custom_k80_ceprintlib")
        self.assertEqual(first.raw_status_hex, "00000000")
        self.assertEqual(second.state, "ready")

    def test_decodes_cover_and_paper_jam(self):
        client = FakeCePrintLibClient(status=0x00030000)
        driver = self.make_driver(client)

        observation = driver.read_status()

        self.assertTrue(observation.cover_open)
        self.assertTrue(observation.print_head_up_or_paper_jam)
        self.assertFalse(observation.paper_jam)
        self.assertEqual(observation.state, "error")

    def test_near_end_explains_that_remaining_centimeters_are_unavailable(self):
        client = FakeCePrintLibClient(status=NEAR_PAPER_END << 24)
        driver = self.make_driver(client)

        observation = driver.read_status()

        self.assertTrue(observation.paper_near_end)
        self.assertIsNone(observation.paper_remaining_cm)
        self.assertFalse(observation.paper_remaining_estimated)
        self.assertIn("no expone", observation.paper_remaining_error)

    def test_decodes_observed_21000000_as_paper_and_ticket_not_cutter(self):
        client = FakeCePrintLibClient(status=0x21000000)
        driver = self.make_driver(client)

        observation = driver.read_status()

        self.assertTrue(observation.paper_out)
        self.assertTrue(observation.ticket_out)
        self.assertFalse(observation.cover_open)
        self.assertFalse(observation.cutter_error)
        self.assertEqual(observation.status_bytes["paper_status_hex"], "21")

    def test_disconnect_deinitializes_the_usb_context(self):
        client = FakeCePrintLibClient()
        driver = self.make_driver(client)
        driver.read_status()

        driver.disconnect()

        self.assertEqual(client.deinitialize_calls, [client.context])

    def test_open_failure_deinitializes_partial_context(self):
        client = FakeCePrintLibClient()
        client.open_error = 130
        driver = self.make_driver(client)

        with self.assertRaisesMessage(PrinterMonitorConnectionError, "No se pudo abrir"):
            driver.read_status()

        self.assertEqual(client.deinitialize_calls, [client.context])

    def test_status_failure_reinitializes_on_next_attempt(self):
        client = FakeCePrintLibClient()
        client.status_error = 140
        driver = self.make_driver(client)

        with self.assertRaisesMessage(PrinterMonitorConnectionError, "fuera de línea"):
            driver.read_status()
        client.status_error = 0
        driver.read_status()

        self.assertEqual(client.initialize_calls, 2)
        self.assertEqual(client.deinitialize_calls, [client.context])

