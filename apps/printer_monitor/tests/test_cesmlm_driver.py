from django.test import SimpleTestCase

from ..drivers.base import PrinterMonitorConnectionError
from ..drivers.cesmlm import CustomK80CeSmLmConfig, CustomK80CeSmLmDriver
from ..drivers.status import CUTTER_ERROR, NEAR_PAPER_END, PAPER_JAM


class FakeCeSmLmClient:
    def __init__(self, *, status=0, remaining_cm=37):
        self.status = status
        self.remaining_cm = remaining_cm
        self.device_count = 1
        self.init_system_error = 0
        self.interface_error = 0
        self.interface_number = 2
        self.gsi_error = 0
        self.gsi_system_error = 0
        self.gsi = 0x1234ABCD
        self.model_error = 0
        self.status_error = 0
        self.status_system_error = 0
        self.remaining_error = 0
        self.remaining_system_error = 0
        self.initialize_calls = 0
        self.status_calls = 0
        self.remaining_calls = 0
        self.names = []

    def initialize_usb(self):
        self.initialize_calls += 1
        return self.device_count, self.init_system_error

    def get_interface_number(self, printer_name):
        self.names.append(printer_name)
        return self.interface_error, self.interface_number

    def get_full_gsi(self, interface_number):
        return self.gsi_error, self.gsi_system_error, self.gsi

    def set_printer_model_gsi(self, interface_number, gsi):
        return self.model_error

    def get_status(self, interface_number):
        self.status_calls += 1
        return self.status_error, self.status_system_error, self.status

    def get_remaining_paper_cm(self, interface_number):
        self.remaining_calls += 1
        return self.remaining_error, self.remaining_system_error, self.remaining_cm


class CustomK80CeSmLmDriverTestCase(SimpleTestCase):
    def make_driver(self, client, **overrides):
        values = {
            "printer_name": "CUSTOM K80",
            "query_remaining_paper": True,
            "mapping_physically_validated": False,
        }
        values.update(overrides)
        return CustomK80CeSmLmDriver(CustomK80CeSmLmConfig(**values), client=client)

    def test_initializes_by_queue_name_and_reuses_interface(self):
        client = FakeCeSmLmClient()
        driver = self.make_driver(client)

        first = driver.read_status()
        second = driver.read_status()

        self.assertEqual(client.initialize_calls, 1)
        self.assertEqual(client.status_calls, 2)
        self.assertEqual(client.names, ["CUSTOM K80"])
        self.assertEqual(first.interface_number, 2)
        self.assertEqual(first.model_gsi_hex, "1234ABCD")
        self.assertEqual(first.raw_status_hex, "00000000")
        self.assertEqual(second.state, "ready")

    def test_queries_estimated_remaining_paper_only_after_near_end(self):
        client = FakeCeSmLmClient(status=NEAR_PAPER_END, remaining_cm=37)
        driver = self.make_driver(client)

        observation = driver.read_status()

        self.assertEqual(client.remaining_calls, 1)
        self.assertEqual(observation.paper_remaining_cm, 37)
        self.assertTrue(observation.paper_remaining_estimated)
        self.assertEqual(observation.state, "warning")

    def test_does_not_query_remaining_paper_before_near_end(self):
        client = FakeCeSmLmClient(status=0)
        driver = self.make_driver(client)

        observation = driver.read_status()

        self.assertEqual(client.remaining_calls, 0)
        self.assertIsNone(observation.paper_remaining_cm)
        self.assertFalse(observation.paper_remaining_estimated)

    def test_remaining_paper_failure_does_not_hide_the_main_status(self):
        client = FakeCeSmLmClient(status=NEAR_PAPER_END)
        client.remaining_error = 50
        driver = self.make_driver(client)

        observation = driver.read_status()

        self.assertTrue(observation.paper_near_end)
        self.assertIsNone(observation.paper_remaining_cm)
        self.assertIn("CeSmLm=50", observation.paper_remaining_error)

    def test_decodes_jam_and_cutter_without_sending_action_commands(self):
        client = FakeCeSmLmClient(status=PAPER_JAM | CUTTER_ERROR)
        driver = self.make_driver(client)

        observation = driver.read_status()

        self.assertTrue(observation.paper_jam)
        self.assertTrue(observation.cutter_error)
        self.assertEqual(observation.state, "error")

    def test_reports_when_no_usb_printer_is_connected(self):
        client = FakeCeSmLmClient()
        client.device_count = 0
        driver = self.make_driver(client)

        with self.assertRaisesMessage(PrinterMonitorConnectionError, "no detectó"):
            driver.read_status()

    def test_status_error_forces_reinitialization_on_next_attempt(self):
        client = FakeCeSmLmClient()
        client.status_error = 330
        driver = self.make_driver(client)

        with self.assertRaises(PrinterMonitorConnectionError):
            driver.read_status()
        client.status_error = 0
        driver.read_status()

        self.assertEqual(client.initialize_calls, 2)

