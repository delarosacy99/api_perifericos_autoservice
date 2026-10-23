from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .drivers import get_card_reader
from .drivers.base import (
    CardReaderConfigurationError,
    CardReaderError,
    DESFireStatusError,
)
from .services import (
    get_card_master_key,
    serialize_credential_verification,
    serialize_inspection,
    serialize_snapshot,
)


def card_reader_error_response(
    exc: CardReaderError,
    *,
    response_status: int = status.HTTP_409_CONFLICT,
) -> Response:
    details = {}
    if isinstance(exc, DESFireStatusError):
        details["status_hex"] = exc.status_hex
    return Response(
        {
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": details,
            }
        },
        status=response_status,
    )


class CardReaderStatusView(APIView):
    def get(self, request):
        return Response(serialize_snapshot(get_card_reader().snapshot()))


class CardInspectView(APIView):
    def post(self, request):
        try:
            inspection = get_card_reader().inspect_card()
        except CardReaderError as exc:
            return card_reader_error_response(exc)
        return Response(serialize_inspection(inspection))


class CardCredentialVerifyView(APIView):
    def post(self, request):
        try:
            verification = get_card_reader().verify_credential(
                master_key=get_card_master_key(),
            )
        except CardReaderConfigurationError as exc:
            return card_reader_error_response(
                exc,
                response_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except CardReaderError as exc:
            return card_reader_error_response(exc)
        return Response(serialize_credential_verification(verification))
