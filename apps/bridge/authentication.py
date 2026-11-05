from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.core.authentication import LocalServiceUser

from .errors import BridgeError
from .lease import verify_screen


def origin_allowed(origin):
    return bool(origin and origin in settings.CORS_ALLOWED_ORIGINS)


class ScreenAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        origin = request.headers.get("Origin")
        if origin and not origin_allowed(origin):
            raise AuthenticationFailed("Origen no autorizado.")
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationFailed("Falta el token temporal de pantalla.")
        token = header[7:]
        try:
            verify_screen(token)
        except BridgeError as exc:
            raise AuthenticationFailed(exc.message, code=exc.code) from None
        return LocalServiceUser(username="screen"), token
