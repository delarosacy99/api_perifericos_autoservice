import json
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from django.conf import settings

from .errors import BridgeError, CentralError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CentralClient:
    """Fixed destination, verified TLS, bounded responses, no secret-bearing logs."""

    def __init__(self):
        self._token = None
        self._expires = 0
        self._lock = threading.RLock()

    def _request(self, method, path, data=None, *, agent=False):
        base = settings.CENTRAL_API_BASE_URL.rstrip("/") + "/"
        url = urlsplit(base)
        if not settings.BRIDGE_ENABLED or not settings.CENTRAL_AGENT_CREDENTIAL:
            raise BridgeError("bridge_not_configured", "Configure la conexión central.", 503)
        if url.username or url.password or url.query or url.fragment or not url.hostname:
            raise BridgeError("invalid_central_url", "URL central inválida.", 503)
        if url.scheme != "https" and not (
            settings.DEBUG
            and url.scheme == "http"
            and url.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise BridgeError("central_tls_required", "La conexión central requiere HTTPS.", 503)
        if path.startswith(("/", "http:", "https:")) or ".." in path:
            raise BridgeError("invalid_central_path", "Ruta central inválida.", 400)
        token = settings.CENTRAL_AGENT_CREDENTIAL if agent else self._token
        scheme = "AutoserviceAgent" if agent else "Autoservice"
        request = Request(
            base + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Authorization": f"{scheme} {token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
            with opener.open(request, timeout=settings.CENTRAL_TIMEOUT_SECONDS) as response:
                body = response.read(settings.CENTRAL_MAX_RESPONSE_BYTES + 1)
                if len(body) > settings.CENTRAL_MAX_RESPONSE_BYTES:
                    raise CentralError("central_response_too_large", uncertain=method != "GET")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("Expected object")
                return result
        except HTTPError as exc:
            # A 5xx/proxy timeout can occur after a successful central commit.
            code = f"central_http_{exc.code}"
            if 400 <= exc.code < 500:
                try:
                    error = json.loads(exc.read(8192))
                    code = error.get("error", {}).get("code", error.get("code", code))
                    if not isinstance(code, str):
                        code = f"central_http_{exc.code}"
                except (ValueError, AttributeError):
                    pass
            raise CentralError(
                code[:120], exc.code, uncertain=exc.code >= 500 or exc.code == 408
            ) from None
        except (URLError, OSError, ValueError):
            raise CentralError("central_unavailable", uncertain=method != "GET") from None

    def bootstrap(self):
        with self._lock:
            result = self._request("POST", "agent/bootstrap/", {}, agent=True)
            if result.get("currency") != settings.CASH_CURRENCY:
                raise BridgeError("currency_mismatch", "Moneda central y billetero diferentes.")
            if not result.get("access_token"):
                raise CentralError("central_invalid_bootstrap")
            self._token = result["access_token"]
            self._expires = time.monotonic() + max(1, int(result.get("expires_in", 900)) - 30)
            return {k: v for k, v in result.items() if k not in {"access_token", "token_type"}}

    def call(self, method, path, data=None, *, agent=False):
        with self._lock:
            if not agent and (not self._token or time.monotonic() >= self._expires):
                self.bootstrap()
            try:
                return self._request(method, path, data, agent=agent)
            except CentralError as exc:
                if not agent and exc.status == 401:
                    self.bootstrap()
                    return self._request(method, path, data, agent=False)
                raise


central = CentralClient()
