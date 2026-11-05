from django.conf import settings
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cash_acceptor.drivers.base import CashAcceptorError
from apps.cash_acceptor.services import CashCollectionError
from apps.core.authentication import LocalAPIKeyAuthentication

from . import services as svc
from .authentication import ScreenAuthentication, origin_allowed
from .central import central
from .errors import BridgeError
from .lease import BRIDGE_LOCK, claim_screen, verify_screen
from .models import PrintJob


class ClaimInput(serializers.Serializer):
    client_id = serializers.UUIDField()


class BeginInput(serializers.Serializer):
    request_key = serializers.UUIDField()


class TicketInput(serializers.Serializer):
    code = serializers.CharField(max_length=2000)
    expected_total = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=0
    )


class FinalizeInput(serializers.Serializer):
    operation = serializers.ChoiceField(choices=["virtual", "lottery", "cancel"])
    game_id = serializers.IntegerField(min_value=1, required=False)
    expected_draw_id = serializers.UUIDField(required=False)
    bets = serializers.ListField(child=serializers.DictField(), max_length=500, required=False)
    plays = serializers.ListField(child=serializers.DictField(), max_length=500, required=False)


class ReportInput(serializers.Serializer):
    attempt_id = serializers.UUIDField()
    outcome = serializers.ChoiceField(choices=["completed", "failed", "uncertain"])
    evidence = serializers.JSONField(default=dict)


class ResolveInput(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["retry", "confirm_printed"])
    reason = serializers.CharField(min_length=5, max_length=500)


def validated(cls, data):
    serializer = cls(data=data)
    serializer.is_valid(raise_exception=True)
    # Convert Decimal/UUID into JSON-safe primitives, preserve central POS contract fields.
    import json

    from rest_framework.renderers import JSONRenderer

    return json.loads(JSONRenderer().render(serializer.validated_data))


class BridgeView(APIView):
    authentication_classes = [ScreenAuthentication]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def dispatch(self, request, *args, **kwargs):
        # Serialize the lease check and operation, including all central calls.
        with BRIDGE_LOCK:
            return super().dispatch(request, *args, **kwargs)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not settings.BRIDGE_ENABLED:
            raise BridgeError("bridge_disabled", "Habilite el puente de estación.", 503)
        origin = request.headers.get("Origin")
        if origin and not origin_allowed(origin):
            raise BridgeError("origin_denied", "Origen no autorizado.", 403)

    def handle_exception(self, exc):
        if isinstance(exc, (BridgeError, CashCollectionError, CashAcceptorError)):
            return Response(
                {"error": {"code": exc.code, "message": str(exc)}},
                status=getattr(exc, "status", 409),
            )
        return super().handle_exception(exc)


class SessionView(BridgeView):
    authentication_classes = [LocalAPIKeyAuthentication]

    def post(self, request):
        data = validated(ClaimInput, request.data)
        return Response(claim_screen(data["client_id"]), status=201)


class HeartbeatView(BridgeView):
    def post(self, request):
        verify_screen(request.auth, renew=True)
        return Response({"alive": True, "expires_in": settings.BRIDGE_LEASE_SECONDS})


class StateView(BridgeView):
    def get(self, request):
        return Response(svc.snapshot())


class BootstrapView(BridgeView):
    def post(self, request):
        return Response(central.bootstrap())


class CatalogView(BridgeView):
    def get(self, request):
        return Response(svc.catalog(request.query_params))


class BeginView(BridgeView):
    def post(self, request):
        import uuid

        data = validated(BeginInput, request.data)
        return Response(svc.begin(uuid.UUID(data["request_key"])))


class RecoverView(BridgeView):
    def post(self, request):
        return Response({"checkout": svc.recover()})


class CheckoutView(BridgeView):
    def get(self, request, checkout_id):
        return Response(svc.refresh(svc.get_checkout(checkout_id)))


class CashStartView(BridgeView):
    def post(self, request, checkout_id):
        return Response(svc.start_cash(svc.get_checkout(checkout_id)))


class SealView(BridgeView):
    def post(self, request, checkout_id):
        return Response(svc.seal(svc.get_checkout(checkout_id)))


class FinalizeView(BridgeView):
    def post(self, request, checkout_id):
        return Response(
            svc.finalize(svc.get_checkout(checkout_id), validated(FinalizeInput, request.data))
        )


class TicketView(BridgeView):
    def post(self, request, checkout_id, action):
        if action not in {"preview", "select", "remove"}:
            raise Http404
        data = validated(TicketInput, request.data)
        return Response(svc.ticket_action(svc.get_checkout(checkout_id), action, data))


def current_job(job_id, *, screen=True):
    job = PrintJob.objects.select_related("checkout").filter(pk=job_id).first()
    if job is None:
        raise Http404
    if screen:
        svc.get_checkout(job.checkout_id)
    return job


class PrintClaimView(BridgeView):
    def post(self, request, job_id):
        return Response(svc.claim_print(current_job(job_id)))


class PrintReportView(BridgeView):
    def post(self, request, job_id):
        import uuid

        data = validated(ReportInput, request.data)
        data["attempt_id"] = uuid.UUID(data["attempt_id"])
        return Response(svc.report_print(current_job(job_id), **data))


class PrintResolveView(BridgeView):
    authentication_classes = [LocalAPIKeyAuthentication]

    def post(self, request, job_id):
        return Response(
            svc.resolve_print(
                current_job(job_id, screen=False), **validated(ResolveInput, request.data)
            )
        )
