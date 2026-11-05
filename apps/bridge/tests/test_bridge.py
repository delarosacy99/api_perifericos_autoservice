import uuid
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bridge import services as svc
from apps.bridge.errors import BridgeError, CentralError
from apps.bridge.lease import claim_screen
from apps.bridge.models import CashDelivery, LocalCheckout, PrintJob, ScreenLease, StreamEvent
from apps.cash_acceptor.drivers import get_cash_acceptor, reset_cash_acceptor
from apps.cash_acceptor.models import AcceptedBanknote
from apps.cash_acceptor.services import record_note_credit


class FakeCentral:
    """Stateful transport double: assertions check the outgoing central contract."""

    def __init__(self):
        self.checkout = None
        self.begin_keys = []
        self.events = {}
        self.finalizations = 0
        self.acks = 0
        self.lose_begin = False
        self.lose_sale = False
        self.fail_cash = False
        self.fail_ack = False

    def bootstrap(self):
        return {
            "currency": "USD",
            "recovery_required": bool(self.checkout and not self.checkout["can_reset"]),
        }

    def call(self, method, path, data=None, *, agent=False):
        if path == "checkouts/":
            assert not agent
            uuid.UUID(data["request_key"])
            uuid.UUID(data["access_key"])
            self.begin_keys.append(dict(data))
            if self.checkout is None:
                self.checkout = {
                    "checkout_id": str(uuid.uuid4()),
                    "status": "open",
                    "currency": "USD",
                    "cash_received": "0.00",
                    "collection_closed": False,
                    "result": {"print_documents": []},
                    "can_reset": False,
                }
            if self.lose_begin:
                self.lose_begin = False
                raise CentralError("timeout", uncertain=True)
        elif path == "agent/cash-events/":
            assert agent
            uuid.UUID(data["event_id"])
            assert data["checkout_id"] == self.checkout["checkout_id"]
            self.events[data["event_id"]] = data["amount_minor"]
            self.checkout["cash_received"] = f"{sum(self.events.values()) / 100:.2f}"
            if self.fail_cash:
                self.fail_cash = False
                raise CentralError("cash_timeout", uncertain=True)
            return {"accepted": True, "checkout_id": self.checkout["checkout_id"]}
        elif path.endswith("/seal/"):
            assert agent
            assert data["received_minor"] == sum(self.events.values())
            assert data["event_count"] == len(self.events)
            self.checkout["collection_closed"] = True
            return {"collection_closed": True}
        elif path.endswith("/finalize/"):
            assert not agent and self.checkout["collection_closed"]
            if self.checkout["status"] == "open":
                self.finalizations += 1
                if data["operation"] == "cancel" and not self.events:
                    self.checkout.update(status="completed", can_reset=True)
                else:
                    kind = "balance" if data["operation"] == "cancel" else "ticket"
                    self.checkout.update(
                        status="print_pending",
                        result={
                            "print_documents": [
                                {"kind": kind, "payload": {"short_code": "TEST", "amount": "2.00"}}
                            ]
                        },
                    )
            if self.lose_sale:
                self.lose_sale = False
                raise CentralError("sale_timeout", uncertain=True)
        elif path.endswith("/print-ack/"):
            assert agent
            self.acks += 1
            assert data["ticket_printed"] or data["voucher_printed"]
            if self.fail_ack:
                self.fail_ack = False
                raise CentralError("ack_timeout", uncertain=True)
            self.checkout.update(status="completed", can_reset=True)
            return {"can_reset": True}
        elif "/tickets/" in path:
            assert not agent and "access_key" in data
            return (
                {"total_available": "800.00", "can_use": True}
                if path.endswith("preview/")
                else dict(self.checkout)
            )
        elif not path.endswith("/status/"):
            raise AssertionError(path)
        return dict(self.checkout)


@override_settings(
    BRIDGE_ENABLED=True,
    CENTRAL_API_BASE_URL="https://central.example/api/autoservice/",
    CENTRAL_AGENT_CREDENTIAL="central-secret",
    CORS_ALLOWED_ORIGINS=["https://kiosk.example"],
    CASH_ACCEPTOR_DRIVER="simulator",
    CASH_CURRENCY="USD",
    CASH_EXPECTED_DATASET_CURRENCY="USD",
    CASH_SIMULATOR_DATASET_CURRENCY="USD",
)
class BridgeTests(TestCase):
    def setUp(self):
        reset_cash_acceptor()
        self.remote = FakeCentral()
        self.patch = patch("apps.bridge.services.central", self.remote)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(reset_cash_acceptor)
        self.token = claim_screen(uuid.uuid4())["screen_token"]
        self.api = APIClient()
        self.api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.token}", HTTP_ORIGIN="https://kiosk.example"
        )

    def begin(self):
        result = svc.begin(uuid.uuid4())
        return LocalCheckout.objects.get(pk=result["local_checkout_id"])

    def note(self, checkout):
        svc.start_cash(checkout)
        event = get_cash_acceptor().simulate_note(currency="USD", denomination_minor=200)
        return record_note_credit(collection_id=checkout.collection_id, note_event=event)[1]

    def evidence(self, job):
        return {
            "state": 128,
            "state-description": "PRINTED",
            "name": f"DEMP-{job.pk}",
            "printer": settings.JSPM_PRINTER_NAME,
        }

    def test_screen_lease_excludes_second_screen(self):
        with self.assertRaisesMessage(BridgeError, "Otra pantalla"):
            claim_screen(uuid.uuid4())

    def test_expired_screen_cannot_command_or_renew(self):
        ScreenLease.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.api.post("/api/v1/bridge/heartbeat/", {}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_screen_token_cannot_call_legacy_hardware_endpoint(self):
        self.assertEqual(
            self.api.post("/api/v1/cash/collections/", {}, format="json").status_code, 403
        )

    def test_origin_denied_and_responses_not_cached(self):
        self.assertEqual(self.api.get("/api/v1/bridge/state/")["Cache-Control"], "no-store")
        self.api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.token}", HTTP_ORIGIN="https://evil.example"
        )
        self.assertEqual(self.api.get("/api/v1/bridge/state/").status_code, 401)

    def test_begin_retries_lost_response_with_identical_keys(self):
        key = uuid.uuid4()
        self.remote.lose_begin = True
        with self.assertRaises(CentralError):
            svc.begin(key)
        payload = svc.begin(key)
        self.assertEqual(self.remote.begin_keys[0], self.remote.begin_keys[1])
        self.assertEqual(payload["checkout_id"], self.remote.checkout["checkout_id"])
        self.assertNotIn("access_key", str(payload))
        self.assertEqual(LocalCheckout.objects.count(), 1)

    def test_second_attention_rejected_before_central_call(self):
        self.begin()
        with self.assertRaises(BridgeError):
            svc.begin(uuid.uuid4())
        self.assertEqual(len(self.remote.begin_keys), 1)

    def test_definite_begin_rejection_does_not_trap_local_recovery(self):
        with patch.object(self.remote, "call", side_effect=CentralError("checkout_pending", 409)):
            with self.assertRaises(CentralError):
                svc.begin(uuid.uuid4())
        self.assertIsNone(svc.active_checkout())

    def test_cash_retry_uses_banknote_uuid_and_counts_once(self):
        checkout = self.begin()
        note = self.note(checkout)
        self.remote.fail_cash = True
        with self.assertRaises(CentralError):
            svc.sync_notes(checkout, force=True)
        self.assertFalse(CashDelivery.objects.get(banknote=note).delivered_at)
        svc.sync_notes(checkout, force=True)
        svc.sync_notes(checkout, force=True)
        self.assertEqual(self.remote.events, {str(note.pk): 200})
        self.assertEqual(CashDelivery.objects.get(banknote=note).attempts, 2)
        self.assertEqual(svc.local_payload(checkout)["pending_cash_events"], 0)

    def test_seal_uses_durable_notes_including_last_credit(self):
        checkout = self.begin()
        self.note(checkout)
        result = svc.seal(checkout)
        self.assertEqual(result["central"]["cash_received"], "2.00")
        self.assertTrue(result["central"]["collection_closed"])
        self.assertEqual(result["collection_status"], "stopped")

    def test_only_ticket_funds_still_seal_zero_cash(self):
        checkout = self.begin()
        svc.seal(checkout)
        self.assertTrue(self.remote.checkout["collection_closed"])
        self.assertEqual(AcceptedBanknote.objects.count(), 0)

    def test_uncertain_sale_rejects_changed_payload_and_recovers_original(self):
        checkout = self.begin()
        self.note(checkout)
        self.remote.lose_sale = True
        payload = {"operation": "virtual", "game_id": 2, "bets": []}
        with self.assertRaises(CentralError):
            svc.finalize(checkout, payload)
        with self.assertRaises(BridgeError):
            svc.finalize(checkout, {"operation": "cancel"})
        result = svc.finalize(checkout, payload)
        self.assertEqual(self.remote.finalizations, 1)
        self.assertEqual(len(result["print_jobs"]), 1)

    def test_cancel_without_cash_needs_no_print_job(self):
        checkout = self.begin()
        result = svc.finalize(checkout, {"operation": "cancel"})
        self.assertTrue(result["central"]["can_reset"])
        self.assertFalse(result["print_jobs"])

    def test_cancel_cash_creates_standalone_balance_document(self):
        checkout = self.begin()
        self.note(checkout)
        result = svc.finalize(checkout, {"operation": "cancel"})
        self.assertEqual(result["print_jobs"][0]["kind"], "balance")

    def test_prize_preview_does_not_create_cash_events(self):
        checkout = self.begin()
        preview = svc.ticket_action(checkout, "preview", {"code": "WINNER"})
        self.assertEqual(preview["total_available"], "800.00")
        self.assertFalse(AcceptedBanknote.objects.exists())

    def issued(self):
        checkout = self.begin()
        self.note(checkout)
        svc.finalize(checkout, {"operation": "virtual", "game_id": 2, "bets": []})
        return checkout, checkout.jobs.get()

    def test_claim_is_once_and_not_print_confirmation(self):
        checkout, job = self.issued()
        svc.claim_print(job)
        self.assertFalse(checkout.completed)
        self.assertEqual(self.remote.acks, 0)
        with self.assertRaises(BridgeError):
            svc.claim_print(job)

    def test_finished_without_positive_spooler_status_does_not_complete(self):
        checkout, job = self.issued()
        svc.claim_print(job)
        with self.assertRaises(BridgeError):
            svc.report_print(job, attempt_id=job.attempt_id, outcome="completed", evidence={})
        self.assertEqual(self.remote.acks, 0)

    def test_error_flag_overrides_printed_and_wrong_printer_rejected(self):
        checkout, job = self.issued()
        evidence = self.evidence(job)
        evidence["state"] = 130
        self.assertFalse(svc.evidence_confirms(job, evidence))
        evidence = self.evidence(job)
        evidence["printer"] = "WRONG"
        self.assertFalse(svc.evidence_confirms(job, evidence))

    def test_report_completion_can_retry_central_ack_without_reprint(self):
        checkout, job = self.issued()
        svc.claim_print(job)
        self.remote.fail_ack = True
        with self.assertRaises(CentralError):
            svc.report_print(
                job, attempt_id=job.attempt_id, outcome="completed", evidence=self.evidence(job)
            )
        job.refresh_from_db()
        self.assertEqual(job.status, "confirmed")
        svc.worker_cycle()
        checkout.refresh_from_db()
        self.assertTrue(checkout.completed)
        self.assertEqual(job.attempts, 1)

    def test_uncertain_print_requires_operator_before_reprint(self):
        checkout, job = self.issued()
        svc.claim_print(job)
        old_attempt = job.attempt_id
        svc.report_print(
            job, attempt_id=old_attempt, outcome="uncertain", evidence={"source": "timeout"}
        )
        with self.assertRaises(BridgeError):
            svc.claim_print(job)
        svc.resolve_print(job, decision="retry", reason="Cola cancelada e impresión no entregada.")
        svc.claim_print(job)
        with self.assertRaises(BridgeError):
            svc.report_print(
                job, attempt_id=old_attempt, outcome="completed", evidence=self.evidence(job)
            )
        self.assertEqual(job.attempts, 2)

    def test_screen_cannot_resolve_print_as_operator(self):
        checkout, job = self.issued()
        response = self.api.post(
            f"/api/v1/bridge/print-jobs/{job.pk}/resolve/",
            {"decision": "retry", "reason": "No impreso"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_watchdog_inhibits_acceptor_without_cancelling_checkout(self):
        checkout = self.begin()
        svc.start_cash(checkout)
        ScreenLease.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        svc.worker_cycle()
        checkout.collection.refresh_from_db()
        self.assertEqual(checkout.collection.status, "stopped")
        self.assertFalse(get_cash_acceptor().snapshot().accepting)
        self.assertFalse(checkout.completed)

    def test_saved_notes_sync_even_if_device_unplugged_after_screen_expiry(self):
        from apps.cash_acceptor.drivers.base import CashAcceptorConnectionError

        checkout = self.begin()
        note = self.note(checkout)
        ScreenLease.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with patch(
            "apps.bridge.services.inhibit_cash",
            side_effect=CashAcceptorConnectionError("USB desconectado"),
        ):
            svc.worker_cycle()
        self.assertTrue(CashDelivery.objects.get(banknote=note).delivered_at)

    def test_watchdog_turns_lost_print_callback_into_uncertain(self):
        checkout, job = self.issued()
        svc.claim_print(job)
        PrintJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(seconds=1000)
        )
        svc.worker_cycle()
        job.refresh_from_db()
        self.assertEqual(job.status, "uncertain")
        self.assertEqual(self.remote.acks, 0)

    def test_local_recovery_preserves_keys_and_unpaid_events(self):
        checkout = self.begin()
        self.note(checkout)
        result = svc.recover()
        checkout.refresh_from_db()
        self.assertEqual(result["local_checkout_id"], str(checkout.pk))
        self.assertEqual(result["pending_cash_events"], 1)
        self.assertEqual(checkout.access_key, LocalCheckout.objects.get(pk=checkout.pk).access_key)

    def test_stream_contains_durable_cash_event(self):
        checkout = self.begin()
        self.note(checkout)
        self.assertTrue(StreamEvent.objects.filter(topic="cash.note.credited").exists())

    def test_new_screen_cannot_retrieve_previous_completed_customer(self):
        checkout = self.begin()
        svc.finalize(checkout, {"operation": "cancel"})
        ScreenLease.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        new_token = claim_screen(uuid.uuid4())["screen_token"]
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {new_token}")
        self.assertEqual(self.api.get(f"/api/v1/bridge/checkouts/{checkout.pk}/").status_code, 404)
        with self.assertRaises(BridgeError):
            svc.begin(checkout.pk)

    def test_end_to_end_http_money_sale_print_and_reset(self):
        response = self.api.post(
            "/api/v1/bridge/checkouts/", {"request_key": str(uuid.uuid4())}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        key = response.data["local_checkout_id"]
        checkout = LocalCheckout.objects.get(pk=key)
        self.note(checkout)
        response = self.api.post(
            f"/api/v1/bridge/checkouts/{key}/finalize/",
            {"operation": "lottery", "plays": []},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        job = checkout.jobs.get()
        claimed = self.api.post(f"/api/v1/bridge/print-jobs/{job.pk}/claim/", {}, format="json")
        response = self.api.post(
            f"/api/v1/bridge/print-jobs/{job.pk}/report/",
            {
                "attempt_id": claimed.data["attempt_id"],
                "outcome": "completed",
                "evidence": self.evidence(job),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["central"]["can_reset"])
        self.assertEqual(self.remote.finalizations, 1)
