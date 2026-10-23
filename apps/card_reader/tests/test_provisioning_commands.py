import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from ..drivers import get_card_reader, reset_card_reader

TEST_MASTER_KEY_HEX = "00112233445566778899AABBCCDDEEFF"
TEST_UID = "04A1B2C3D4E5F6"
TEST_CREDENTIAL_ID = "4dc84d82-93ef-4d26-a1d1-e399b686a5e5"


@override_settings(CARD_READER_DRIVER="simulator")
class CardProvisioningCommandTestCase(SimpleTestCase):
    def setUp(self):
        reset_card_reader()

    def tearDown(self):
        reset_card_reader()

    def test_dry_run_never_modifies_the_card(self):
        output = StringIO()

        call_command(
            "provision_test_card",
            confirm_uid=TEST_UID,
            credential_id=TEST_CREDENTIAL_ID,
            stdout=output,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "dry_run")
        self.assertFalse(payload["card_was_modified"])
        self.assertEqual(payload["current_state"], "blank")
        self.assertEqual(get_card_reader().inspect_card().application_ids, ())

    @override_settings(CARD_ALLOW_TEST_PROVISIONING=False, DEBUG=True)
    def test_execute_is_blocked_by_default(self):
        with self.assertRaisesMessage(CommandError, "La escritura está bloqueada"):
            call_command(
                "provision_test_card",
                confirm_uid=TEST_UID,
                credential_id=TEST_CREDENTIAL_ID,
                execute=True,
            )
        self.assertEqual(get_card_reader().inspect_card().application_ids, ())

    @override_settings(
        CARD_ALLOW_TEST_PROVISIONING=True,
        CARD_PROVISIONING_MASTER_KEY_HEX=TEST_MASTER_KEY_HEX,
        DEBUG=True,
    )
    def test_execute_and_read_only_verification(self):
        provision_output = StringIO()
        call_command(
            "provision_test_card",
            confirm_uid=TEST_UID,
            credential_id=TEST_CREDENTIAL_ID,
            execute=True,
            stdout=provision_output,
        )

        provisioned = json.loads(provision_output.getvalue())
        self.assertTrue(provisioned["verified"])
        self.assertTrue(provisioned["keys_personalized"])
        self.assertNotIn("master_key", provisioned)

        verify_output = StringIO()
        call_command("verify_card_credential", stdout=verify_output)
        verified = json.loads(verify_output.getvalue())
        self.assertTrue(verified["authenticated"])
        self.assertEqual(verified["credential_id"], TEST_CREDENTIAL_ID)

    @override_settings(
        CARD_ALLOW_TEST_PROVISIONING=True,
        CARD_PROVISIONING_MASTER_KEY_HEX=TEST_MASTER_KEY_HEX,
        DEBUG=True,
    )
    def test_exact_uid_confirmation_is_required(self):
        with self.assertRaisesMessage(CommandError, "se esperaba 04112233445566"):
            call_command(
                "provision_test_card",
                confirm_uid="04112233445566",
                credential_id=TEST_CREDENTIAL_ID,
                execute=True,
            )
        self.assertEqual(get_card_reader().inspect_card().application_ids, ())

    @override_settings(
        CARD_ALLOW_TEST_PROVISIONING=True,
        CARD_PROVISIONING_MASTER_KEY_HEX="00000000000000000000000000000000",
        DEBUG=True,
    )
    def test_zero_master_key_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "no puede ser la clave AES cero"):
            call_command(
                "provision_test_card",
                confirm_uid=TEST_UID,
                credential_id=TEST_CREDENTIAL_ID,
                execute=True,
            )
