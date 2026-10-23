from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .drivers import get_door_monitor
from .drivers.base import DoorMonitorError
from .services import serialize_observation


def door_monitor_error_response(exc: DoorMonitorError) -> Response:
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


class DoorStatusView(APIView):
    def get(self, request):
        try:
            observation = get_door_monitor().read_status()
        except DoorMonitorError as exc:
            return door_monitor_error_response(exc)
        return Response(serialize_observation(observation))
