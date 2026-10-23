from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ..drivers import get_printer_monitor, reset_printer_monitor
from ..drivers.base import PrinterMonitorConnectionError


@override_settings(
    PRINTER_MONITOR_DRIVER="simulator",
    PRINTER_SIMULATOR_RAW_STATUS=0,
    PRINTER_SIMULATOR_PAPER_REMAINING_CM=None,
    CUSTOM_K80_PRINTER_NAME="CUSTOM K80",
    CUSTOM_K80_QUERY_REMAINING_PAPER=True,
    CUSTOM_K80_STATUS_MAPPING_VALIDATED=False,
)
class PrinterMonitorAPITestCase(APITestCase):
    def setUp(self):
        reset_printer_monitor()
        self.client.credentials(HTTP_X_LOCAL_API_KEY=settings.LOCAL_API_KEY)

    def tearDown(self):
        reset_printer_monitor()

    def test_status_requires_local_api_key(self):
        self.client.credentials()

        response = self.client.get(reverse("printer-status"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_status_returns_read_only_observation(self):
        response = self.client.get(reverse("printer-status"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["device_id"], "printer")
        self.assertEqual(response.data["model"], "CUSTOM-K80-SIMULATOR")
        self.assertEqual(response.data["state"], "ready")
        self.assertTrue(response.data["ready"])
        self.assertTrue(response.data["monitoring_only"])
        self.assertEqual(response.data["raw_status_hex"], "00000000")
        self.assertFalse(response.data["mapping_physically_validated"])
        self.assertIn("observed_at", response.data)

    def test_status_endpoint_does_not_offer_print_cut_or_feed(self):
        response = self.client.post(reverse("printer-status"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_hardware_error_returns_service_unavailable(self):
        monitor = get_printer_monitor()
        with patch.object(
            monitor,
            "read_status",
            side_effect=PrinterMonitorConnectionError("sin impresora"),
        ):
            response = self.client.get(reverse("printer-status"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["error"]["code"],
            "printer_monitor_connection_error",
        )

    def test_station_configuration_declares_monitoring_without_output(self):
        response = self.client.get(reverse("station-configuration"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["printer"]["interface"], "usb")
        self.assertTrue(response.data["printer"]["monitoring_only"])
        self.assertFalse(response.data["printer"]["printing_enabled"])
        self.assertFalse(response.data["printer"]["remaining_paper_query_enabled"])
        self.assertFalse(response.data["printer"]["remaining_paper_estimation_supported"])
        self.assertFalse(response.data["printer"]["status_mapping_physically_validated"])
        self.assertTrue(response.data["capabilities"]["printer_monitoring"])
        self.assertFalse(response.data["capabilities"]["printer_output"])

