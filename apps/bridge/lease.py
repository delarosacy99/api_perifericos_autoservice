import hashlib
import hmac
import secrets
import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .errors import BridgeError
from .models import ScreenLease, StreamEvent

BRIDGE_LOCK = threading.RLock()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def lease_alive():
    return ScreenLease.objects.filter(pk=1, expires_at__gt=timezone.now()).exists()


def claim_screen(client_id):
    with BRIDGE_LOCK:
        lease, _ = ScreenLease.objects.get_or_create(pk=1)
        if lease.expires_at and lease.expires_at > timezone.now():
            raise BridgeError("screen_in_use", "Otra pantalla controla esta estación.")
        from .services import inhibit_cash

        inhibit_cash()
        token = secrets.token_urlsafe(32)
        lease.client_id = client_id
        lease.token_hash = digest(token)
        lease.expires_at = timezone.now() + timedelta(seconds=settings.BRIDGE_LEASE_SECONDS)
        from .services import active_checkout

        lease.checkout = active_checkout()
        last = StreamEvent.objects.order_by("-id").first()
        lease.event_floor = last.pk if last else 0
        lease.save()
        return {
            "screen_token": token,
            "expires_in": settings.BRIDGE_LEASE_SECONDS,
            "heartbeat_seconds": settings.BRIDGE_HEARTBEAT_SECONDS,
            "event_cursor": lease.event_floor,
            "websocket_path": "/ws/v1/bridge/",
        }


def verify_screen(token, *, renew=False):
    with BRIDGE_LOCK:
        lease = ScreenLease.objects.filter(pk=1).first()
        if (
            not token
            or not lease
            or not lease.token_hash
            or not hmac.compare_digest(lease.token_hash, digest(token))
            or not lease.expires_at
            or lease.expires_at <= timezone.now()
        ):
            raise BridgeError("screen_session_expired", "Recupere la sesión de pantalla.", 401)
        if renew:
            lease.expires_at = timezone.now() + timedelta(seconds=settings.BRIDGE_LEASE_SECONDS)
            lease.save(update_fields=["expires_at"])
        return lease
