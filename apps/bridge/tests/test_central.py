import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, override_settings

from apps.bridge.central import CentralClient, NoRedirect
from apps.bridge.errors import BridgeError, CentralError


@override_settings(
    BRIDGE_ENABLED=True,
    CENTRAL_API_BASE_URL="https://central.example/api/autoservice/",
    CENTRAL_AGENT_CREDENTIAL="permanent",
    CASH_CURRENCY="USD",
)
class CentralClientTests(SimpleTestCase):
    def response(self, data):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(data).encode()
        return response

    def test_bootstrap_filters_permanent_and_temporary_credentials(self):
        with patch("apps.bridge.central.build_opener") as opener:
            opener.return_value.open.return_value = self.response(
                {"access_token": "ephemeral", "currency": "USD", "expires_in": 900}
            )
            client = CentralClient()
            result = client.bootstrap()
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.headers["Authorization"], "AutoserviceAgent permanent")
            self.assertNotIn("access_token", result)
            self.assertEqual(client._token, "ephemeral")

    def test_permanent_credentials_not_sent_to_redirect(self):
        self.assertIsNone(
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example")
        )

    @override_settings(DEBUG=False, CENTRAL_API_BASE_URL="http://central.example/api/autoservice/")
    def test_production_http_rejected_before_request(self):
        with self.assertRaisesMessage(BridgeError, "HTTPS"):
            CentralClient().bootstrap()

    def test_timeout_is_uncertain_and_does_not_contain_secrets(self):
        with patch("apps.bridge.central.build_opener") as opener:
            opener.return_value.open.side_effect = URLError("private request contents")
            with self.assertRaises(CentralError) as caught:
                CentralClient().bootstrap()
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn("private", str(caught.exception))

    def test_401_refreshes_temporary_token_and_retries_identical_request(self):
        client = CentralClient()
        client._token = "old"
        client._expires = float("inf")
        error = HTTPError("https://central.example", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        with patch("apps.bridge.central.build_opener") as opener:
            opener.return_value.open.side_effect = [
                error,
                self.response({"access_token": "new", "currency": "USD"}),
                self.response({"ok": True}),
            ]
            data = {"request_key": "same"}
            self.assertEqual(client.call("POST", "checkouts/", data), {"ok": True})
            calls = opener.return_value.open.call_args_list
            self.assertEqual(calls[0].args[0].data, calls[2].args[0].data)
            self.assertEqual(calls[2].args[0].headers["Authorization"], "Autoservice new")

    def test_bootstrap_currency_mismatch_blocks_station(self):
        with patch("apps.bridge.central.build_opener") as opener:
            opener.return_value.open.return_value = self.response(
                {"access_token": "x", "currency": "DOP"}
            )
            with self.assertRaises(BridgeError):
                CentralClient().bootstrap()
