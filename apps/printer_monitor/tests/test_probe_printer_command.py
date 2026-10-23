import json
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from ..drivers import reset_printer_monitor


@override_settings(
    PRINTER_MONITOR_DRIVER="simulator",
    PRINTER_SIMULATOR_RAW_STATUS=0,
    PRINTER_SIMULATOR_PAPER_REMAINING_CM=None,
    CUSTOM_K80_PRINTER_NAME="CUSTOM K80",
    CUSTOM_K80_STATUS_MAPPING_VALIDATED=False,
)
class ProbePrinterCommandTestCase(SimpleTestCase):
    def setUp(self):
        reset_printer_monitor()

    def tearDown(self):
        reset_printer_monitor()

    @patch("apps.printer_monitor.management.commands.probe_printer.time.sleep")
    def test_probe_is_read_only_and_returns_requested_samples(self, sleep):
        output = StringIO()

        call_command("probe_printer", samples=2, interval=0.5, stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "read_only")
        self.assertEqual(payload["full_status_query_hex"], "10 04 14")
        self.assertEqual(
            payload["actions_excluded"],
            ["print", "paper_feed", "cut", "reset"],
        )
        self.assertEqual(len(payload["samples"]), 2)
        self.assertTrue(all(item["monitoring_only"] for item in payload["samples"]))
        sleep.assert_called_once_with(0.5)

    def test_probe_rejects_too_short_repeated_interval(self):
        with self.assertRaisesMessage(CommandError, "al menos 0.5"):
            call_command("probe_printer", samples=2, interval=0.1)

    def test_probe_rejects_unbounded_sample_count(self):
        with self.assertRaisesMessage(CommandError, "--samples"):
            call_command("probe_printer", samples=101, interval=0.5)

