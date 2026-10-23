from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ..drivers import get_door_monitor, reset_door_monitor
from ..drivers.base import DoorMonitorConnectionError


@override_settings(
    DOOR_MONITOR_DRIVER="simulator",
    DOOR_MONITOR_BACKGROUND_ENABLED=False,
    DOOR_SIMULATOR_STATE="closed",
    XL128_DOOR_OPEN_RESPONSE_CODES=[],
    XL128_DOOR_CLOSED_RESPONSE_CODES=[],
)
class DoorMonitorAPITestCase(APITestCase):
    def setUp(self):
        reset_door_monitor()
        self.client.credentials(HTTP_X_LOCAL_API_KEY=settings.LOCAL_API_KEY)

    def tearDown(self):
        reset_door_monitor()

    def test_status_requires_local_api_key(self):
        self.client.credentials()

        response = self.client.get(reverse("door-status"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_status_returns_live_monitoring_only_observation(self):
        response = self.client.get(reverse("door-status"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["door_id"], "front")
        self.assertEqual(response.data["door_name"], "Puerta frontal")
        self.assertEqual(response.data["coverage"], "front_door_only")
        self.assertEqual(response.data["driver"], "simulator")
        self.assertTrue(response.data["connected"])
        self.assertTrue(response.data["monitoring_only"])
        self.assertEqual(response.data["state"], "closed")
        self.assertFalse(response.data["is_open"])
        self.assertEqual(response.data["raw_status_hex"], "00")
        self.assertTrue(response.data["physically_calibrated_status"])
        self.assertIn("observed_at", response.data)

    def test_status_does_not_offer_an_actuation_method(self):
        response = self.client.post(reverse("door-status"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_hardware_timeout_returns_service_unavailable(self):
        monitor = get_door_monitor()
        with patch.object(
            monitor,
            "read_status",
            side_effect=DoorMonitorConnectionError("sin respuesta"),
        ):
            response = self.client.get(reverse("door-status"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["error"]["code"],
            "door_monitor_connection_error",
        )

    def test_station_configuration_declares_monitoring_without_actuation(self):
        response = self.client.get(reverse("station-configuration"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["door"]["id"], "front")
        self.assertEqual(response.data["door"]["coverage"], "front_door_only")
        self.assertTrue(response.data["door"]["monitoring_only"])
        self.assertFalse(response.data["door"]["background_monitoring_enabled"])
        self.assertEqual(response.data["door"]["poll_interval_seconds"], 0.5)
        self.assertEqual(response.data["door"]["debounce_samples"], 2)
        self.assertFalse(response.data["door"]["state_mapping_configured"])
        self.assertTrue(response.data["capabilities"]["door_monitoring"])
        self.assertFalse(response.data["capabilities"]["door_actuation"])
