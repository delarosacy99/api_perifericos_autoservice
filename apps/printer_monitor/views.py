from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .drivers import get_printer_monitor
from .drivers.base import PrinterMonitorError
from .services import serialize_observation


def printer_monitor_error_response(exc: PrinterMonitorError) -> Response:
    return Response(
        {
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": {},
            }
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class PrinterStatusView(APIView):
    def get(self, request):
        try:
            observation = get_printer_monitor().read_status()
        except PrinterMonitorError as exc:
            return printer_monitor_error_response(exc)
        return Response(serialize_observation(observation))

