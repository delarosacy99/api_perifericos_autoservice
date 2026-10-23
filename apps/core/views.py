from django.conf import settings
from django.db import connection
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

        return Response(
            {
                "status": "ok",
                "service": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "station_id": settings.STATION_ID,
                "timestamp": timezone.now(),
            }
        )
