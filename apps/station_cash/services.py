import threading
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.cash_acceptor.drivers import get_cash_acceptor
from apps.cash_acceptor.models import AcceptedBanknote, CashCollectionSession
from apps.cash_acceptor.services import create_collection, start_collection, stop_collection
from apps.outbox.models import OutboxEvent
from apps.outbox.services import enqueue_event
from .central import CashSyncError, central_call
from .models import AttentionBinding

STATION_LOCK = threading.RLock()


def serialized(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with STATION_LOCK:
            return fn(*args, **kwargs)
    return run


def binding_for(attention_id):
    binding = AttentionBinding.objects.select_related("collection").filter(pk=attention_id).first()
    if binding is None:
        raise CashSyncError("local_attention_not_found", "La atención todavía no tiene una recepción local.")
    return binding


def snapshot(binding):
    collection = CashCollectionSession.objects.get(pk=binding.collection_id)
    pending = OutboxEvent.objects.filter(aggregate_id=str(collection.pk), topic="cash.note.credited", delivered_at__isnull=True)
    return {
        "attention_id": str(binding.pk), "collection_id": str(collection.pk),
        "currency": collection.currency, "collection_status": collection.status,
        "cash_received_local_minor": collection.accepted_amount_minor,
        "pending_cash_events": pending.count(), "sealed": binding.sealed,
        "central": binding.central_snapshot, "last_error": binding.last_error,
    }


def heartbeat(attention_id):
    if not AttentionBinding.objects.filter(pk=attention_id).update(heartbeat_at=timezone.now()):
        raise CashSyncError("local_attention_not_found")
    return {"alive": True}


def notify(binding, topic, data):
    enqueue_event(topic=topic, aggregate_type="cash_collection", aggregate_id=str(binding.collection_id), payload={"attention_id": str(binding.pk), **data})


@serialized
def start_attention(attention_id):
    state = central_call(attention_id, data={})
    if state.get("currency") != settings.CASH_CURRENCY:
        raise CashSyncError("currency_mismatch", "La moneda central no coincide con el billetero.")
    binding = AttentionBinding.objects.filter(pk=attention_id).first()
    if binding is None:
        if state.get("received_minor"):
            raise CashSyncError("local_journal_missing", "El saldo central existe y no se encontró su diario local. Solicite asistencia.")
        other = AttentionBinding.objects.filter(sealed=False).exclude(pk=attention_id).first()
        if other:
            raise CashSyncError("local_attention_pending", "Detenga y concilie la recepción anterior.")
        with transaction.atomic():
            collection, _ = create_collection(idempotency_key=f"attention:{attention_id}", remote_session_id=str(attention_id), currency=state["currency"], metadata={"owner": "station_cash"})
            binding = AttentionBinding.objects.create(attention_id=attention_id, collection=collection, heartbeat_at=timezone.now(), central_snapshot=state)
    if binding.sealed:
        raise CashSyncError("attention_sealed")
    heartbeat(attention_id)
    start_collection(binding.collection_id)
    binding.central_snapshot = state
    binding.save(update_fields=["central_snapshot"])
    return snapshot(binding)


def inhibit(binding, reason="frontend_disconnected"):
    collection = CashCollectionSession.objects.get(pk=binding.collection_id)
    if collection.status != "stopped":
        stop_collection(collection.pk, reason=reason)


def sync_notes(binding, *, force=False):
    if not force and binding.retry_at and binding.retry_at > timezone.now():
        return
    events = OutboxEvent.objects.filter(aggregate_id=str(binding.collection_id), topic="cash.note.credited", delivered_at__isnull=True).order_by("occurred_at", "id")
    for event in events:
        note = AcceptedBanknote.objects.get(pk=event.payload["accepted_banknote_id"], session_id=binding.collection_id)
        event.attempts += 1
        try:
            state = central_call(binding.pk, "bills/", {"event_id": str(note.pk), "amount_minor": note.denomination_minor, "currency": note.currency})
            if not state.get("accepted") or state.get("currency") != note.currency:
                raise CashSyncError("central_bill_not_confirmed")
        except CashSyncError as exc:
            event.last_error = exc.code
            event.save(update_fields=["attempts", "last_error"])
            binding.last_error = exc.code
            binding.retry_at = timezone.now() + timedelta(seconds=min(30, 2 ** min(event.attempts, 5)))
            binding.save(update_fields=["last_error", "retry_at"])
            inhibit(binding, reason="device_error")
            notify(binding, "station.cash.pending", {"code": exc.code})
            raise
        event.delivered_at = timezone.now()
        event.last_error = ""
        event.save(update_fields=["attempts", "last_error", "delivered_at"])
        binding.central_snapshot = state
        binding.retry_at = None
        binding.last_error = ""
        binding.save(update_fields=["central_snapshot", "retry_at", "last_error"])
        notify(binding, "station.cash.synced", {"event_id": str(note.pk), "amount_minor": note.denomination_minor})


@serialized
def stop_attention(attention_id):
    binding = AttentionBinding.objects.filter(pk=attention_id).first()
    if binding is None:
        state = central_call(attention_id)
        if state.get("received_minor") or state.get("currency") != settings.CASH_CURRENCY or CashCollectionSession.objects.filter(status="accepting").exists():
            raise CashSyncError("local_journal_missing")
        with transaction.atomic():
            collection, _ = create_collection(idempotency_key=f"attention:{attention_id}", remote_session_id=str(attention_id), currency=state["currency"], metadata={"owner": "station_cash"})
            binding = AttentionBinding.objects.create(attention_id=attention_id, collection=collection, heartbeat_at=timezone.now(), central_snapshot=state)
    inhibit(binding, reason="remote_completed")
    sync_notes(binding, force=True)
    notes = AcceptedBanknote.objects.filter(session_id=binding.collection_id)
    state = central_call(binding.pk, "seal/", {"received_minor": notes.aggregate(total=Sum("denomination_minor"))["total"] or 0, "event_count": notes.count()})
    if not state.get("collection_closed"):
        raise CashSyncError("central_not_sealed")
    binding.central_snapshot = state
    binding.sealed = True
    binding.last_error = ""
    binding.save(update_fields=["central_snapshot", "sealed", "last_error"])
    notify(binding, "station.collection.sealed", {})
    return snapshot(binding)


@serialized
def initialize_hardware():
    # A process restart never resumes acceptance without a live frontend.
    driver = get_cash_acceptor()
    driver.connect()
    driver.disable()
    for binding in AttentionBinding.objects.filter(sealed=False):
        inhibit(binding)
    for collection in CashCollectionSession.objects.filter(status="accepting"):
        stop_collection(collection.pk, reason="service_shutdown")


@serialized
def worker_cycle():
    for binding in AttentionBinding.objects.filter(sealed=False):
        if binding.heartbeat_at < timezone.now() - timedelta(seconds=settings.FRONTEND_HEARTBEAT_TIMEOUT_SECONDS):
            try:
                inhibit(binding)
            except Exception:
                # Already durable notes must still synchronize with USB unplugged.
                pass
        sync_notes(binding)
        # A live browser alone does not authorize continued acceptance. Probe the
        # central permission even when no new banknote has arrived.
        if CashCollectionSession.objects.filter(pk=binding.collection_id, status="accepting").exists():
            try:
                central_call(binding.pk, data={})
            except CashSyncError as exc:
                binding.last_error = exc.code
                binding.save(update_fields=["last_error"])
                inhibit(binding, reason="device_error")
                notify(binding, "station.cash.pending", {"code": exc.code})
                raise
