from django.conf import settings
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cash_acceptor.drivers.base import CashAcceptorError
from apps.cash_acceptor.services import CashCollectionError
from apps.cash_acceptor.serializers import StrictSerializer
from . import services
from .central import CashSyncError


class StartInput(StrictSerializer):
    attention_id = serializers.UUIDField()


class StationView(APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not settings.CENTRAL_SYNC_ENABLED:
            raise CashSyncError("station_sync_disabled")
        origin = request.headers.get("Origin")
        if origin and origin not in settings.CORS_ALLOWED_ORIGINS:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Origen local no autorizado.")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def handle_exception(self, exc):
        if isinstance(exc, (CashSyncError, CashCollectionError, CashAcceptorError)):
            return Response({"error": {"code": exc.code, "message": str(exc)}}, status=409)
        return super().handle_exception(exc)


class AttentionStartView(StationView):
    def post(self, request):
        data = StartInput(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(services.start_attention(data.validated_data["attention_id"]))


class AttentionStateView(StationView):
    def get(self, request, attention_id):
        return Response(services.snapshot(services.binding_for(attention_id)))


class AttentionHeartbeatView(StationView):
    def post(self, request, attention_id):
        return Response(services.heartbeat(attention_id))


class AttentionStopView(StationView):
    def post(self, request, attention_id):
        return Response(services.stop_attention(attention_id))
