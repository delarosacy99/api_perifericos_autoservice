import logging
import time
from dataclasses import asdict
from functools import wraps
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.outbox.services import enqueue_event

from .drivers import get_cash_acceptor
from .drivers.base import NoteCreditEvent
from .models import AcceptedBanknote, CashCollectionSession

logger = logging.getLogger(__name__)


class CashCollectionError(Exception):
    code = "cash_collection_error"
    http_status = 409

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class IdempotencyConflict(CashCollectionError):
    code = "idempotency_conflict"


class InvalidCollectionState(CashCollectionError):
    code = "invalid_collection_state"


class ActiveCollectionExists(CashCollectionError):
    code = "active_collection_exists"


class DatasetCurrencyMismatch(CashCollectionError):
    code = "dataset_currency_mismatch"


class CashboxRemoved(CashCollectionError):
    code = "cashbox_removed"


class PendingDeviceCredit(CashCollectionError):
    code = "pending_device_credit"


def inhibit_on_failure(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if settings.CENTRAL_SYNC_ENABLED:
                from .runtime import get_cash_acceptor_poller
                get_cash_acceptor_poller().fail_closed()
            raise
    return wrapped


def collection_payload(collection: CashCollectionSession) -> dict[str, Any]:
    return {
        "collection_id": str(collection.id),
        "remote_session_id": collection.remote_session_id,
        "currency": collection.currency,
        "accepted_amount_minor": collection.accepted_amount_minor,
        "status": collection.status,
        "stop_reason": collection.stop_reason,
    }


def create_collection(
    *,
    idempotency_key: str,
    remote_session_id: str,
    currency: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[CashCollectionSession, bool]:
    normalized_currency = currency.upper()
    expected_values = {
        "currency": normalized_currency,
        "remote_session_id": remote_session_id,
    }

    with transaction.atomic():
        existing = CashCollectionSession.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            conflicts = {
                field: {"existing": getattr(existing, field), "received": value}
                for field, value in expected_values.items()
                if getattr(existing, field) != value
            }
            if conflicts:
                raise IdempotencyConflict(
                    "La clave de idempotencia ya fue utilizada con otros datos.",
                    details={"conflicts": conflicts},
                )
            return existing, False

        if normalized_currency != settings.CASH_CURRENCY:
            raise DatasetCurrencyMismatch(
                "La moneda solicitada no coincide con la moneda configurada en la estación.",
                details={
                    "requested_currency": normalized_currency,
                    "station_currency": settings.CASH_CURRENCY,
                },
            )

        try:
            collection = CashCollectionSession.objects.create(
                idempotency_key=idempotency_key,
                remote_session_id=remote_session_id,
                currency=normalized_currency,
                metadata=metadata or {},
            )
        except IntegrityError:
            collection = CashCollectionSession.objects.get(idempotency_key=idempotency_key)
            return collection, False

        enqueue_event(
            topic="cash.collection.created",
            aggregate_type="cash_collection",
            aggregate_id=str(collection.id),
            payload=collection_payload(collection),
        )
        return collection, True


def connect_and_enable_collection_hardware(
    collection: CashCollectionSession,
    *,
    driver=None,
) -> tuple[Any, tuple[int, ...]]:
    """Connect and configure the validator for an existing local collection.

    This operation is shared by the initial start and the polling recovery path so
    currency/dataset checks cannot diverge after a USB or power interruption.
    """

    driver = driver or get_cash_acceptor()
    snapshot = driver.connect()
    expected_currency = settings.CASH_EXPECTED_DATASET_CURRENCY
    if expected_currency not in snapshot.currencies:
        driver.disable()
        raise DatasetCurrencyMismatch(
            "El dataset del billetero no contiene la moneda esperada.",
            details={
                "expected_currency": expected_currency,
                "reported_currencies": sorted(snapshot.currencies),
                "dataset_version": snapshot.dataset_version,
            },
        )
    if collection.currency not in snapshot.currencies:
        driver.disable()
        raise DatasetCurrencyMismatch(
            "El dataset del billetero no contiene la moneda de la recolección.",
            details={
                "collection_currency": collection.currency,
                "reported_currencies": sorted(snapshot.currencies),
            },
        )

    # A disabled eSSP POLL gives us the current cashbox signal before ENABLE. This
    # prevents a newly-created collection from briefly accepting with no cashbox.
    # Recovery of an already-active collection skips this preflight so a pending
    # POLL WITH ACK credit remains available to the normal durable-credit path.
    if collection.status == CashCollectionSession.Status.CREATED and driver.supports_polling:
        preflight_batch = driver.poll_once(allow_disabled=True)
        for state_change in preflight_batch.state_changes:
            if state_change == "CASHBOX_REMOVED":
                logger.error("Alerta NV200: CASHBOX_REMOVED (código 0xE3)")
            elif state_change == "CASHBOX_REPLACED":
                logger.info("Estado NV200: CASHBOX_REPLACED (código 0xE4)")
        if preflight_batch.note_credits:
            driver.disable()
            raise PendingDeviceCredit(
                "El NV200 reportó un crédito pendiente antes de iniciar la recolección.",
                details={"credit_count": len(preflight_batch.note_credits)},
            )
        snapshot = driver.snapshot()

    if snapshot.cashbox_present is False:
        driver.disable()
        raise CashboxRemoved(
            "No se puede iniciar la recolección porque el cashbox está retirado.",
            details={"cashbox_present": False},
        )

    allowed_denominations = None
    if not settings.CASH_ACCEPT_ALL_DATASET_DENOMINATIONS:
        allowed_denominations = set(settings.CASH_ACCEPTED_DENOMINATIONS_MINOR)

    enabled_channels = driver.enable_currency(
        collection.currency,
        denominations_minor=allowed_denominations,
    )
    if not enabled_channels:
        driver.disable()
        raise DatasetCurrencyMismatch(
            "No existen canales habilitables para la moneda configurada.",
            details={"currency": collection.currency},
        )
    return driver.snapshot(), enabled_channels


@inhibit_on_failure
def start_collection(collection_id) -> CashCollectionSession:
    driver = get_cash_acceptor()
    from .runtime import CASH_ACCEPTOR_OPERATION_LOCK, get_cash_acceptor_poller

    with CASH_ACCEPTOR_OPERATION_LOCK:
        hardware_touched = False
        try:
            with transaction.atomic():
                collection = CashCollectionSession.objects.select_for_update().get(pk=collection_id)
                if collection.status == CashCollectionSession.Status.ACCEPTING:
                    if collection.metadata.get("inhibit_requested") or get_cash_acceptor_poller().faulted:
                        raise InvalidCollectionState(
                            "El cierre del billetero sigue pendiente de conciliar."
                        )
                    return collection
                if collection.status != CashCollectionSession.Status.CREATED:
                    raise InvalidCollectionState(
                        "Solamente una recolección creada puede comenzar a aceptar efectivo.",
                        details={"status": collection.status},
                    )

                active_collection = (
                    CashCollectionSession.objects.select_for_update()
                    .filter(status=CashCollectionSession.Status.ACCEPTING)
                    .exclude(pk=collection.pk)
                    .first()
                )
                if active_collection is not None:
                    raise ActiveCollectionExists(
                        "Ya existe otra recolección controlando el billetero.",
                        details={"active_collection_id": str(active_collection.id)},
                    )

                hardware_touched = True
                _snapshot, enabled_channels = connect_and_enable_collection_hardware(
                    collection,
                    driver=driver,
                )

                collection.status = CashCollectionSession.Status.ACCEPTING
                collection.started_at = timezone.now()
                collection.save(update_fields=["status", "started_at", "updated_at"])
                enqueue_event(
                    topic="cash.collection.started",
                    aggregate_type="cash_collection",
                    aggregate_id=str(collection.id),
                    payload={
                        **collection_payload(collection),
                        "enabled_channels": list(enabled_channels),
                    },
                )
                transaction.on_commit(get_cash_acceptor_poller().resume)
                return collection
        except Exception:
            if hardware_touched:
                driver.disable()
            raise


def record_note_credit(
    *,
    collection_id,
    note_event: NoteCreditEvent,
) -> tuple[CashCollectionSession, AcceptedBanknote, bool]:
    with transaction.atomic():
        collection = CashCollectionSession.objects.select_for_update().get(pk=collection_id)
        existing = AcceptedBanknote.objects.filter(device_event_id=note_event.event_id).first()
        if existing is not None:
            if (existing.session_id != collection.id or existing.currency != note_event.currency
                or existing.denomination_minor != note_event.denomination_minor):
                raise IdempotencyConflict(
                    "El evento del billetero ya pertenece a otra recolección.",
                    details={"device_event_id": note_event.event_id},
                )
            return collection, existing, False

        if collection.status != CashCollectionSession.Status.ACCEPTING:
            raise InvalidCollectionState(
                "La recolección no está aceptando efectivo.",
                details={"status": collection.status},
            )
        if note_event.currency != collection.currency:
            raise DatasetCurrencyMismatch(
                "La moneda del billete no coincide con la recolección.",
                details={
                    "collection_currency": collection.currency,
                    "note_currency": note_event.currency,
                },
            )

        accepted_banknote = AcceptedBanknote.objects.create(
            session=collection,
            device_event_id=note_event.event_id,
            channel=note_event.channel,
            currency=note_event.currency,
            denomination_minor=note_event.denomination_minor,
            raw_event=note_event.raw_event,
        )
        collection.accepted_amount_minor += note_event.denomination_minor
        collection.save(update_fields=["accepted_amount_minor", "updated_at"])
        enqueue_event(
            topic="cash.note.credited",
            aggregate_type="cash_collection",
            aggregate_id=str(collection.id),
            payload={
                **collection_payload(collection),
                "accepted_banknote_id": str(accepted_banknote.id),
                "device_event_id": accepted_banknote.device_event_id,
                "channel": accepted_banknote.channel,
                "denomination_minor": accepted_banknote.denomination_minor,
            },
        )
        return collection, accepted_banknote, True


@inhibit_on_failure
def stop_collection(
    collection_id,
    *,
    reason: str,
) -> CashCollectionSession:
    driver = get_cash_acceptor()
    from .runtime import CASH_ACCEPTOR_OPERATION_LOCK

    with CASH_ACCEPTOR_OPERATION_LOCK:
        collection = CashCollectionSession.objects.get(pk=collection_id)
        if collection.status == CashCollectionSession.Status.STOPPED:
            return collection
        if collection.status not in {
            CashCollectionSession.Status.CREATED,
            CashCollectionSession.Status.ACCEPTING,
            CashCollectionSession.Status.INTERRUPTED,
        }:
            raise InvalidCollectionState(
                "La recolección no puede detenerse en su estado actual.",
                details={"status": collection.status},
            )

        was_accepting = collection.status == CashCollectionSession.Status.ACCEPTING
        if was_accepting:
            # The polling loop uses the same lock, so no NOTE CREDIT can be committed
            # concurrently after the collection is marked stopped.
            if driver.supports_polling and not driver.snapshot().connected:
                driver.connect()
            driver.disable()
            collection.metadata = {**collection.metadata, "inhibit_requested": True}
            collection.save(update_fields=["metadata", "updated_at"])
            if driver.supports_polling:
                # POLL remains valid while the validator is disabled. Drain any credit
                # already emitted before closing the local collection.
                durable_mode = settings.CENTRAL_SYNC_ENABLED and bool(
                    getattr(getattr(driver, "config", None), "journal_path", "")
                )
                quiet_since = time.monotonic()
                deadline = quiet_since + 3.0
                for _attempt in range(100 if durable_mode else 3):
                    batch = driver.poll_once(allow_disabled=True)
                    for note_credit in batch.note_credits:
                        record_note_credit(
                            collection_id=collection.id,
                            note_event=note_credit,
                        )
                    if batch.requires_ack:
                        driver.acknowledge_events(batch.batch_id)
                    busy = bool(getattr(driver, "note_inflight", False))
                    if batch.note_credits or busy:
                        quiet_since = time.monotonic()
                    if not durable_mode and not batch.note_credits:
                        break
                    if durable_mode:
                        if not busy and time.monotonic() - quiet_since >= 0.3:
                            break
                        if time.monotonic() >= deadline:
                            raise PendingDeviceCredit(
                                "El billetero aún procesa un billete. Espere la conciliación."
                            )
                        time.sleep(0.03)
                else:
                    if durable_mode:
                        raise PendingDeviceCredit("El billetero requiere revisión antes de cerrar.")
                collection.refresh_from_db()
        with transaction.atomic():
            collection.status = CashCollectionSession.Status.STOPPED
            collection.stop_reason = reason
            collection.stopped_at = timezone.now()
            collection.save(update_fields=["status", "stop_reason", "stopped_at", "updated_at"])
            enqueue_event(
                topic="cash.collection.stopped",
                aggregate_type="cash_collection",
                aggregate_id=str(collection.id),
                payload=collection_payload(collection),
            )
        return collection


def serialize_snapshot(snapshot) -> dict[str, Any]:
    data = asdict(snapshot)
    data["currencies"] = sorted(snapshot.currencies)
    data["channels"] = [asdict(channel) for channel in snapshot.channels]
    data["alerts"] = list(snapshot.alerts)
    return data
