import logging
import uuid
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.cash_acceptor.models import AcceptedBanknote, CashCollectionSession
from apps.cash_acceptor.services import create_collection, start_collection, stop_collection

from .central import central
from .errors import BridgeError, CentralError
from .lease import BRIDGE_LOCK, lease_alive
from .models import CashDelivery, LocalCheckout, PrintJob, ScreenLease, StreamEvent

logger = logging.getLogger(__name__)


def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with BRIDGE_LOCK:
            return fn(*args, **kwargs)

    return wrapped


def emit(topic, payload):
    StreamEvent.objects.create(topic=topic, payload=payload)


def active_checkout():
    return LocalCheckout.objects.filter(completed=False).order_by("created_at").first()


def get_checkout(checkout_id):
    checkout = LocalCheckout.objects.filter(pk=checkout_id).first()
    if checkout is None:
        raise BridgeError("checkout_not_found", "No existe esa atención local.", 404)
    # Only the newest attention is accessible to the public screen interface.
    newest = LocalCheckout.objects.order_by("-created_at").values_list("pk", flat=True).first()
    if checkout.pk != newest or not ScreenLease.objects.filter(pk=1, checkout=checkout).exists():
        raise BridgeError("checkout_not_current", "La atención pertenece a otro cliente.", 404)
    return checkout


def job_payload(job):
    return {
        "job_id": str(job.pk),
        "kind": job.kind,
        "status": job.status,
        "attempts": job.attempts,
        "attempt_id": str(job.attempt_id) if job.attempt_id else None,
        "document_name": f"DEMP-{job.pk}",
        "printer_name": settings.JSPM_PRINTER_NAME,
        "document": job.document,
    }


def local_payload(checkout):
    collection = checkout.collection
    notes = (
        AcceptedBanknote.objects.filter(session=collection)
        if collection
        else AcceptedBanknote.objects.none()
    )
    pending = notes.exclude(cashdelivery__delivered_at__isnull=False)
    return {
        "local_checkout_id": str(checkout.pk),
        "checkout_id": str(checkout.remote_id) if checkout.remote_id else None,
        "collection_id": str(collection.pk) if collection else None,
        "collection_status": collection.status if collection else None,
        "cash_received_local_minor": collection.accepted_amount_minor if collection else 0,
        "pending_cash_events": pending.count(),
        "pending_cash_minor": pending.aggregate(total=Sum("denomination_minor"))["total"] or 0,
        "central": checkout.snapshot,
        "finalize_uncertain": checkout.finalize_payload is not None
        and not checkout.finalize_confirmed,
        "last_error": checkout.last_error,
        "print_jobs": [job_payload(job) for job in checkout.jobs.order_by("pk")],
    }


def snapshot():
    checkout = active_checkout()
    return {
        "station_id": settings.STATION_ID,
        "version": settings.APP_VERSION,
        "printing": {"provider": "jsprintmanager", "printer_name": settings.JSPM_PRINTER_NAME},
        "checkout": local_payload(checkout) if checkout else None,
    }


def save_snapshot(checkout, data):
    if str(data.get("checkout_id")) != str(checkout.remote_id):
        raise CentralError("central_checkout_mismatch", uncertain=True)
    checkout.snapshot = data
    checkout.last_error = ""
    checkout.completed = bool(data.get("can_reset"))
    if data.get("status") in {"print_pending", "completed"}:
        checkout.finalize_confirmed = True
    checkout.save()
    for document in data.get("result", {}).get("print_documents", []):
        if document.get("kind") not in {"ticket", "balance"}:
            raise BridgeError("unsupported_print_document", "Documento central no compatible.")
        # Snapshot immutable once a job exists. Refreshed balance state must not mutate a dispatched paper.
        PrintJob.objects.get_or_create(
            checkout=checkout, kind=document["kind"], defaults={"document": document}
        )
    emit(
        "bridge.checkout.updated",
        {"local_checkout_id": str(checkout.pk), "status": data.get("status")},
    )


def remote_status(checkout):
    return central.call(
        "POST", f"checkouts/{checkout.remote_id}/status/", {"access_key": str(checkout.access_key)}
    )


def attach_collection(checkout):
    if checkout.collection_id:
        return
    collection, _ = create_collection(
        idempotency_key=f"bridge:{checkout.pk}",
        remote_session_id=str(checkout.remote_id),
        currency=settings.CASH_CURRENCY,
        metadata={"owner": "bridge"},
    )
    checkout.collection = collection
    checkout.save(update_fields=["collection"])


@serialized
def begin(request_key):
    current = active_checkout()
    if current and current.pk != request_key:
        raise BridgeError(
            "checkout_pending", "Recupere la atención pendiente antes de iniciar otra."
        )
    checkout, _ = LocalCheckout.objects.get_or_create(pk=request_key)
    if checkout.completed and not ScreenLease.objects.filter(pk=1, checkout=checkout).exists():
        raise BridgeError(
            "checkout_not_current", "Inicie una atención nueva para este cliente.", 404
        )
    ScreenLease.objects.filter(pk=1).update(checkout=checkout)
    if checkout.remote_id:
        return local_payload(checkout)
    # Request/access keys persisted BEFORE HTTP. Lost responses are retried with the same keys.
    try:
        data = central.call(
            "POST",
            "checkouts/",
            {"request_key": str(checkout.pk), "access_key": str(checkout.access_key)},
        )
    except CentralError as exc:
        if not exc.uncertain and 400 <= exc.status < 500:
            checkout.completed = True
            checkout.last_error = exc.code
            checkout.save(update_fields=["completed", "last_error"])
        raise
    checkout.remote_id = data["checkout_id"]
    checkout.save(update_fields=["remote_id"])
    save_snapshot(checkout, data)
    attach_collection(checkout)
    return local_payload(checkout)


@serialized
def refresh(checkout):
    if checkout.remote_id is None:
        return begin(checkout.pk)
    save_snapshot(checkout, remote_status(checkout))
    return local_payload(checkout)


@serialized
def recover():
    inhibit_cash()
    checkout = active_checkout()
    if checkout:
        ScreenLease.objects.filter(pk=1).update(checkout=checkout)
        if not checkout.remote_id:
            return begin(checkout.pk)
        # Keep local keys after a restart; this also discovers a completed print-ack whose response was lost.
        try:
            return refresh(checkout)
        except CentralError as exc:
            if exc.status not in {403, 404}:
                raise
    config = central.bootstrap()
    if not config.get("recovery_required"):
        return None
    key = checkout.access_key if checkout else uuid.uuid4()
    data = central.call("POST", "agent/recover/", {"access_key": str(key)}, agent=True)
    if checkout and checkout.remote_id and str(checkout.remote_id) != str(data["checkout_id"]):
        raise BridgeError(
            "recovery_mismatch", "Atención central diferente; requiere conciliación técnica."
        )
    if checkout is None:
        checkout = LocalCheckout.objects.create(
            id=uuid.uuid4(), remote_id=data["checkout_id"], access_key=key
        )
    ScreenLease.objects.filter(pk=1).update(checkout=checkout)
    save_snapshot(checkout, data)
    attach_collection(checkout)
    # Reconciliation will reject seal if the local journal is missing; never fabricate cash events.
    return local_payload(checkout)


def inhibit_cash():
    collections = list(
        CashCollectionSession.objects.filter(status=CashCollectionSession.Status.ACCEPTING)
    )
    for collection in collections:
        stop_collection(collection.pk, reason="frontend_disconnected")
    if not collections:
        from apps.cash_acceptor.drivers import get_cash_acceptor

        driver = get_cash_acceptor()
        driver.connect()
        driver.disable()


@serialized
def start_cash(checkout):
    if not lease_alive():
        raise BridgeError("screen_session_expired", "La pantalla no tiene control activo.", 401)
    if checkout.finalize_payload is not None:
        raise BridgeError("finalize_pending", "Resuelva la emisión antes de aceptar efectivo.")
    save_snapshot(checkout, remote_status(checkout))
    if checkout.snapshot.get("status") != "open" or checkout.snapshot.get("collection_closed"):
        raise BridgeError("collection_closed", "Esta atención ya cerró la recepción de efectivo.")
    attach_collection(checkout)
    # A stopped collection is deliberately never reopened (resume by tickets/cancel, no new cash).
    start_collection(checkout.collection_id)
    checkout.collection.refresh_from_db()
    return local_payload(checkout)


def sync_notes(checkout, *, force=False):
    if not checkout.collection_id or not checkout.remote_id:
        return
    changed = False
    for note in checkout.collection.accepted_banknotes.order_by("accepted_at", "pk"):
        delivery, _ = CashDelivery.objects.get_or_create(banknote=note)
        if delivery.delivered_at or (
            not force and delivery.next_attempt_at and delivery.next_attempt_at > timezone.now()
        ):
            continue
        delivery.attempts += 1
        try:
            result = central.call(
                "POST",
                "agent/cash-events/",
                {
                    "checkout_id": str(checkout.remote_id),
                    "event_id": str(note.pk),
                    "amount_minor": note.denomination_minor,
                },
                agent=True,
            )
            if not result.get("accepted") or str(result.get("checkout_id")) != str(
                checkout.remote_id
            ):
                raise CentralError("central_cash_not_confirmed", uncertain=True)
        except BridgeError as exc:
            delivery.last_error = exc.code
            delivery.next_attempt_at = timezone.now() + timedelta(
                seconds=min(60, 2 ** min(delivery.attempts, 6))
            )
            delivery.save()
            checkout.last_error = exc.code
            checkout.save(update_fields=["last_error"])
            # Stop receiving more money while an existing note awaits confirmation.
            inhibit_cash()
            raise
        delivery.delivered_at = timezone.now()
        delivery.next_attempt_at = None
        delivery.last_error = ""
        delivery.save()
        changed = True
        emit(
            "bridge.cash.synced",
            {
                "local_checkout_id": str(checkout.pk),
                "event_id": str(note.pk),
                "amount_minor": note.denomination_minor,
            },
        )
    if changed:
        save_snapshot(checkout, remote_status(checkout))


@serialized
def seal(checkout):
    if checkout.remote_id is None:
        raise BridgeError("checkout_pending", "La API central aún no creó la atención.")
    attach_collection(checkout)
    stop_collection(checkout.collection_id, reason="remote_completed")
    sync_notes(checkout, force=True)
    notes = checkout.collection.accepted_banknotes
    total = notes.aggregate(total=Sum("denomination_minor"))["total"] or 0
    central.call(
        "POST",
        f"agent/checkouts/{checkout.remote_id}/seal/",
        {
            "received_minor": total,
            "event_count": notes.count(),
        },
        agent=True,
    )
    save_snapshot(checkout, remote_status(checkout))
    checkout.collection.refresh_from_db()
    return local_payload(checkout)


@serialized
def finalize(checkout, payload):
    if checkout.finalize_payload is not None and checkout.finalize_payload != payload:
        raise BridgeError(
            "finalize_conflict", "Reintente exactamente la misma confirmación pendiente."
        )
    if checkout.finalize_confirmed:
        return local_payload(checkout)
    seal(checkout)
    checkout.finalize_payload = payload
    checkout.save(update_fields=["finalize_payload"])
    try:
        data = central.call(
            "POST",
            f"checkouts/{checkout.remote_id}/finalize/",
            {
                **payload,
                "access_key": str(checkout.access_key),
            },
        )
    except CentralError as exc:
        if not exc.uncertain and 400 <= exc.status < 500:
            checkout.finalize_payload = None
        checkout.last_error = exc.code
        checkout.save()
        raise
    save_snapshot(checkout, data)
    checkout.finalize_confirmed = True
    checkout.save(update_fields=["finalize_confirmed"])
    return local_payload(checkout)


@serialized
def ticket_action(checkout, action, payload):
    if checkout.finalize_payload is not None or checkout.completed:
        raise BridgeError("finalize_pending", "Ya existe una emisión en curso.")
    suffix = {"preview": "tickets/preview/", "select": "tickets/", "remove": "tickets/remove/"}[
        action
    ]
    data = central.call(
        "POST",
        f"checkouts/{checkout.remote_id}/{suffix}",
        {**payload, "access_key": str(checkout.access_key)},
    )
    if action == "preview":
        return data
    save_snapshot(checkout, data)
    return local_payload(checkout)


def catalog(params):
    query = urlencode({k: params[k] for k in ("game_id", "product") if k in params})
    return central.call("GET", "catalog/?" + query)


def maybe_ack_print(checkout):
    jobs = list(checkout.jobs.all())
    if (
        checkout.completed
        or not jobs
        or any(job.status != PrintJob.Status.CONFIRMED for job in jobs)
    ):
        return
    # Only this code holds the permanent agent credential. Acknowledgment is idempotent centrally.
    result = central.call(
        "POST",
        f"agent/checkouts/{checkout.remote_id}/print-ack/",
        {
            "ticket_printed": any(j.kind == "ticket" for j in jobs),
            "voucher_printed": any(j.kind == "balance" for j in jobs),
        },
        agent=True,
    )
    if not result.get("can_reset"):
        raise CentralError("central_print_not_confirmed", uncertain=True)
    save_snapshot(checkout, remote_status(checkout))


@serialized
def claim_print(job):
    if job.checkout.completed or job.status != PrintJob.Status.READY:
        raise BridgeError(
            "print_already_claimed", "El documento ya fue enviado o requiere revisión."
        )
    job.attempt_id = uuid.uuid4()
    job.attempts += 1
    job.status = PrintJob.Status.DISPATCHING
    job.save()
    emit("bridge.print.dispatching", {"job_id": str(job.pk)})
    return job_payload(job)


def evidence_confirms(job, evidence):
    if not isinstance(evidence, dict):
        return False
    # Windows spooler flags. ERROR/OFFLINE/PAPEROUT/deletion trump a success flag.
    raw = evidence.get("state")
    valid_state = isinstance(raw, int) and not isinstance(raw, bool)
    flags = {s.strip().upper() for s in str(evidence.get("state-description", "")).split(",")}
    failures = {
        "ERROR",
        "OFFLINE",
        "PAPEROUT",
        "DELETING",
        "DELETED",
        "BLOCKED_DEVQ",
        "USER_INTERVENTION",
        "PAUSED",
    }
    if flags & failures or (
        valid_state and raw & (0x1 | 0x2 | 0x4 | 0x20 | 0x40 | 0x100 | 0x200 | 0x400)
    ):
        return False
    success = bool(flags & {"PRINTED", "COMPLETE"}) or (valid_state and bool(raw & (0x80 | 0x1000)))
    return bool(
        success
        and evidence.get("name") == f"DEMP-{job.pk}"
        and evidence.get("printer") == settings.JSPM_PRINTER_NAME
    )


@serialized
def report_print(job, *, attempt_id, outcome, evidence):
    if job.attempt_id != attempt_id:
        raise BridgeError(
            "print_attempt_mismatch", "El aviso pertenece a otro intento de impresión."
        )
    if job.status == PrintJob.Status.CONFIRMED:
        maybe_ack_print(job.checkout)
        return local_payload(job.checkout)
    if job.status not in {PrintJob.Status.DISPATCHING, PrintJob.Status.UNCERTAIN}:
        raise BridgeError("print_not_dispatching", "El documento requiere revisión.")
    if outcome == "completed" and not evidence_confirms(job, evidence):
        raise BridgeError("print_not_proven", "No hay confirmación válida de la cola de impresión.")
    job.evidence = evidence
    job.status = {"completed": "confirmed", "failed": "failed", "uncertain": "uncertain"}[outcome]
    job.save()
    emit("bridge.print.updated", {"job_id": str(job.pk), "status": job.status})
    maybe_ack_print(job.checkout)
    return local_payload(job.checkout)


@serialized
def resolve_print(job, *, decision, reason):
    if job.status == PrintJob.Status.DISPATCHING:
        raise BridgeError(
            "print_still_dispatching", "Espere el resultado o el vencimiento del intento."
        )
    if job.status not in {PrintJob.Status.UNCERTAIN, PrintJob.Status.FAILED}:
        raise BridgeError("print_resolution_not_required", "El documento no requiere resolución.")
    job.resolution_reason = reason
    job.evidence = {"source": "operator", "decision": decision, "reason": reason}
    job.status = "ready" if decision == "retry" else "confirmed"
    # Invalidate late callbacks immediately on retry.
    if decision == "retry":
        job.attempt_id = None
    job.save()
    emit("bridge.print.resolved", {"job_id": str(job.pk), "decision": decision})
    maybe_ack_print(job.checkout)
    return job_payload(job)


@serialized
def worker_cycle():
    if not lease_alive():
        from apps.cash_acceptor.drivers.base import CashAcceptorError
        from apps.cash_acceptor.services import CashCollectionError

        try:
            inhibit_cash()
        except (CashAcceptorError, CashCollectionError) as exc:
            # A disconnected device must not block delivery of already-durable notes.
            logger.warning("Recepción inhibida pendiente de hardware: %s", exc.code)
    for job in PrintJob.objects.filter(
        status="dispatching",
        updated_at__lt=timezone.now() - timedelta(seconds=settings.BRIDGE_PRINT_TIMEOUT_SECONDS),
    ):
        job.status = "uncertain"
        job.save(update_fields=["status", "updated_at"])
        emit("bridge.print.updated", {"job_id": str(job.pk), "status": "uncertain"})
    checkout = active_checkout()
    if checkout and checkout.remote_id:
        sync_notes(checkout)
        maybe_ack_print(checkout)
