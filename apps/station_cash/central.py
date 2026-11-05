import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from django.conf import settings


class CashSyncError(Exception):
    def __init__(self, code, message="No se confirmó la sincronización central."):
        self.code = code
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def central_call(attention_id, suffix="", data=None):
    base = settings.CENTRAL_API_BASE_URL.rstrip("/") + "/"
    url = urlsplit(base)
    if not settings.CENTRAL_SYNC_ENABLED or not settings.CENTRAL_AGENT_CREDENTIAL:
        raise CashSyncError("central_not_configured")
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        raise CashSyncError("invalid_central_url")
    if url.scheme != "https" and not (settings.DEBUG and url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise CashSyncError("central_https_required")
    # Callers supply only UUIDs and fixed suffixes, never arbitrary URLs.
    request = Request(base + f"agent/attentions/{attention_id}/" + suffix,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Authorization": "AutoserviceAgent " + settings.CENTRAL_AGENT_CREDENTIAL, "Content-Type": "application/json"},
        method="GET" if data is None else "POST")
    try:
        opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request, timeout=settings.CENTRAL_TIMEOUT_SECONDS) as response:
            body = response.read(1_048_577)
            if len(body) > 1_048_576:
                raise CashSyncError("central_response_too_large")
            value = json.loads(body)
            if not isinstance(value, dict) or str(value.get("attention_id")) != str(attention_id):
                raise CashSyncError("central_invalid_response")
            return value
    except HTTPError as exc:
        code = f"central_http_{exc.code}"
        try:
            value = json.loads(exc.read(8192))
            if isinstance(value.get("code"), str):
                code = value["code"][:120]
        except (ValueError, AttributeError):
            pass
        raise CashSyncError(code) from None
    except (URLError, OSError, ValueError):
        # Do not log headers, credentials, request bodies, URLs or raw server errors.
        raise CashSyncError("central_unavailable") from None
