from django.conf import settings
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from .drivers import get_cash_acceptor
from .drivers.base import CashAcceptorError
from .models import CashCollectionSession
from .serializers import (
    CashCollectionSerializer,
    CreateCashCollectionSerializer,
    SimulateNoteSerializer,
    StopCashCollectionSerializer,
)
from .services import (
    CashCollectionError,
    create_collection,
    record_note_credit,
    serialize_snapshot,
    start_collection,
    stop_collection,
)


def error_response(exc: Exception) -> Response:
    if isinstance(exc, CashCollectionError):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
            status=exc.http_status,
        )
    if isinstance(exc, CashAcceptorError):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": {},
                }
            },
            status=status.HTTP_409_CONFLICT,
        )
    raise exc


class CashCollectionViewSet(GenericViewSet):
    queryset = CashCollectionSession.objects.prefetch_related("accepted_banknotes").all()
    serializer_class = CashCollectionSerializer

    def list(self, request):
        queryset = self.get_queryset()[:100]
        return Response(self.get_serializer(queryset, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def create(self, request):
        input_serializer = CreateCashCollectionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            collection, created = create_collection(**input_serializer.validated_data)
        except CashCollectionError as exc:
            return error_response(exc)

        output = self.get_serializer(collection)
        return Response(
            output.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        try:
            collection = start_collection(pk)
        except CashCollectionSession.DoesNotExist as exc:
            raise Http404 from exc
        except (CashCollectionError, CashAcceptorError) as exc:
            return error_response(exc)
        return Response(self.get_serializer(collection).data)

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        input_serializer = StopCashCollectionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            collection = stop_collection(pk, **input_serializer.validated_data)
        except CashCollectionSession.DoesNotExist as exc:
            raise Http404 from exc
        except (CashCollectionError, CashAcceptorError) as exc:
            return error_response(exc)
        return Response(self.get_serializer(collection).data)


class CashAcceptorStatusView(APIView):
    def get(self, request):
        driver = get_cash_acceptor()
        return Response(serialize_snapshot(driver.snapshot()))


class SimulateNoteView(APIView):
    def post(self, request):
        if settings.CASH_ACCEPTOR_DRIVER != "simulator":
            return Response(
                {
                    "error": {
                        "code": "simulator_disabled",
                        "message": "El simulador del billetero está deshabilitado.",
                        "details": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SimulateNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        collection_id = serializer.validated_data["collection_session_id"]
        denomination_minor = serializer.validated_data["denomination_minor"]

        try:
            collection = CashCollectionSession.objects.get(pk=collection_id)
            driver = get_cash_acceptor()
            note_event = driver.simulate_note(
                currency=collection.currency,
                denomination_minor=denomination_minor,
            )
            updated_collection, accepted_banknote, created = record_note_credit(
                collection_id=collection.id,
                note_event=note_event,
            )
        except CashCollectionSession.DoesNotExist as exc:
            raise Http404 from exc
        except (CashCollectionError, CashAcceptorError) as exc:
            return error_response(exc)

        return Response(
            {
                "created": created,
                "accepted_banknote_id": str(accepted_banknote.id),
                "collection": CashCollectionSerializer(updated_collection).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
