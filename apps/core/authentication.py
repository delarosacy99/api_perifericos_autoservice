from dataclasses import dataclass
from hmac import compare_digest

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


@dataclass(frozen=True)
class LocalServiceUser:
    username: str = "local-service"
    is_active: bool = True
    is_authenticated: bool = True


class LocalAPIKeyAuthentication(BaseAuthentication):
    header_name = "X-Local-API-Key"

    def authenticate(self, request):
        received_key = request.headers.get(self.header_name, "")
        expected_key = settings.LOCAL_API_KEY

        if not received_key:
            raise AuthenticationFailed("Falta X-Local-API-Key.", code="missing_local_api_key")
        if not compare_digest(received_key, expected_key):
            raise AuthenticationFailed("X-Local-API-Key inválida.", code="invalid_local_api_key")

        return LocalServiceUser(), None
