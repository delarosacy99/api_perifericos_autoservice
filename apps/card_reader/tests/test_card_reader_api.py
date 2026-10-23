from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ..drivers import get_card_reader, reset_card_reader

TEST_MASTER_KEY_HEX = "00112233445566778899AABBCCDDEEFF"
TEST_MASTER_KEY = bytes.fromhex(TEST_MASTER_KEY_HEX)
TEST_UID = "04A1B2C3D4E5F6"
TEST_CREDENTIAL_ID = "f0457aa7-e552-4ce8-b32f-f669bcc40b37"


@override_settings(
    CARD_READER_DRIVER="simulator",
    CARD_PROVISIONING_MASTER_KEY_HEX=TEST_MASTER_KEY_HEX,
)
class CardReaderAPITestCase(APITestCase):
    def setUp(self):
        reset_card_reader()
        self.client.credentials(HTTP_X_LOCAL_API_KEY=settings.LOCAL_API_KEY)

    def tearDown(self):
        reset_card_reader()

    def test_status_requires_local_api_key(self):
        self.client.credentials()

        response = self.client.get(reverse("card-reader-status"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_status_does_not_open_reader(self):
        response = self.client.get(reverse("card-reader-status"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["driver"], "simulator")
        self.assertFalse(response.data["connected"])
        self.assertFalse(response.data["card_present"])

    def test_inspection_reports_blank_ev3_without_membership_data(self):
        response = self.client.post(reverse("card-reader-inspect"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["card_type"], "mifare_desfire_ev3")
        self.assertEqual(response.data["capacity_bytes"], 8192)
        self.assertEqual(response.data["free_memory_bytes"], 8192)
        self.assertEqual(response.data["application_ids"], ())
        self.assertEqual(response.data["credential_state"], "blank")
        self.assertNotIn("balance", response.data)
        self.assertNotIn("member", response.data)
        self.assertNotIn("customer", response.data)

    def test_station_configuration_keeps_membership_outside_local_agent(self):
        response = self.client.get(reverse("station-configuration"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["capabilities"]["card_inspection"])
        self.assertFalse(response.data["capabilities"]["rfid_membership"])
        self.assertFalse(response.data["capabilities"]["card_credential_provisioning"])
        self.assertTrue(response.data["capabilities"]["card_credential_verification"])
        self.assertTrue(response.data["capabilities"]["card_credential_verification_configured"])

    def provision_simulated_card(self, *, master_key=TEST_MASTER_KEY):
        return get_card_reader().provision_card(
            expected_uid=TEST_UID,
            credential_id=TEST_CREDENTIAL_ID,
            master_key=master_key,
        )

    def test_credential_verification_requires_local_api_key(self):
        self.provision_simulated_card()
        self.client.credentials()

        response = self.client.post(
            reverse("card-reader-credential-verify"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_credential_verification_returns_only_authenticated_opaque_identity(self):
        self.provision_simulated_card()

        response = self.client.post(
            reverse("card-reader-credential-verify"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "uid": TEST_UID,
                "application_id": "D3E001",
                "credential_id": TEST_CREDENTIAL_ID,
                "authenticated": True,
                "credential_version": 1,
            },
        )
        self.assertNotIn("balance", response.data)
        self.assertNotIn("member", response.data)
        self.assertNotIn("customer", response.data)
        self.assertNotIn("master_key", response.data)

    def test_blank_card_is_rejected_without_membership_data(self):
        response = self.client.post(
            reverse("card-reader-credential-verify"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["error"]["code"],
            "card_reader_protocol_error",
        )

    @override_settings(CARD_PROVISIONING_MASTER_KEY_HEX="")
    def test_missing_master_key_reports_service_unavailable(self):
        response = self.client.post(
            reverse("card-reader-credential-verify"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["error"]["code"],
            "card_reader_configuration_error",
        )
        self.assertNotIn(TEST_MASTER_KEY_HEX, str(response.data))

    @override_settings(CARD_PROVISIONING_MASTER_KEY_HEX="FFEEDDCCBBAA99887766554433221100")
    def test_wrong_master_key_rejects_credential(self):
        self.provision_simulated_card()

        response = self.client.post(
            reverse("card-reader-credential-verify"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["error"]["code"],
            "card_reader_protocol_error",
        )

    def test_credential_verification_does_not_accept_get(self):
        response = self.client.get(reverse("card-reader-credential-verify"))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
