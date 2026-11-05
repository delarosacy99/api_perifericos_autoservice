from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cash_acceptor.drivers import get_cash_acceptor, reset_cash_acceptor
from apps.cash_acceptor.models import AcceptedBanknote, CashCollectionSession
from apps.cash_acceptor.runtime import get_cash_acceptor_poller, reset_cash_acceptor_poller
from apps.cash_acceptor.services import record_note_credit, InvalidCollectionState, stop_collection, IdempotencyConflict
from apps.outbox.models import OutboxEvent
from apps.station_cash.central import CashSyncError
from apps.station_cash.models import AttentionBinding
from apps.station_cash import services as cash


class FakeCentral:
    def __init__(self):
        self.attention_id = uuid4()
        self.currency = "USD"
        self.credits = {}
        self.closed = False
        self.offline = False
        self.lose_reply = False
        self.revoked = False

    def state(self):
        return {"attention_id": str(self.attention_id), "currency": self.currency,
                "received_minor": sum(self.credits.values()), "balance_minor": sum(self.credits.values()),
                "event_count": len(self.credits), "collection_closed": self.closed,
                "status": "sealed" if self.closed else "open"}

    def __call__(self, attention_id, suffix="", data=None):
        if self.offline:
            raise CashSyncError("central_unavailable")
        if str(attention_id) != str(self.attention_id):
            raise CashSyncError("attention_not_found")
        if suffix == "bills/":
            self.credits.setdefault(data["event_id"], data["amount_minor"])
            if self.lose_reply:
                self.lose_reply = False
                raise CashSyncError("central_unavailable")
            return {**self.state(), "accepted": True}
        if suffix == "seal/":
            if data["received_minor"] != sum(self.credits.values()) or data["event_count"] != len(self.credits):
                raise CashSyncError("cash_reconciliation_required")
            self.closed = True
        elif data is not None and (self.closed or self.revoked):
            raise CashSyncError("station_blocked")
        return self.state()


@override_settings(CENTRAL_SYNC_ENABLED=True, DEBUG=True,
    CASH_ACCEPTOR_DRIVER="simulator", CASH_CURRENCY="USD", CASH_EXPECTED_DATASET_CURRENCY="USD",
    CASH_SIMULATOR_DATASET_CURRENCY="USD", CASH_ACCEPTED_DENOMINATIONS_MINOR=(100, 500, 1000),
    CORS_ALLOWED_ORIGINS=["https://kiosk.example.test"], FRONTEND_HEARTBEAT_TIMEOUT_SECONDS=20)
class CashSyncTests(TestCase):
    def setUp(self):
        reset_cash_acceptor_poller()
        reset_cash_acceptor()
        self.central = FakeCentral()
        self.stub = patch("apps.station_cash.services.central_call", side_effect=self.central)
        self.stub.start()
        self.addCleanup(self.stub.stop)
        self.addCleanup(reset_cash_acceptor_poller)
        self.addCleanup(reset_cash_acceptor)
        self.client = APIClient()
        self.client.credentials(HTTP_X_LOCAL_API_KEY=settings.LOCAL_API_KEY, HTTP_ORIGIN="https://kiosk.example.test")

    def start(self):
        cash.start_attention(self.central.attention_id)
        return AttentionBinding.objects.get(pk=self.central.attention_id)

    def note(self, binding):
        event = get_cash_acceptor().simulate_note(currency="USD", denomination_minor=500)
        return record_note_credit(collection_id=binding.collection_id, note_event=event)[1]

    def test_start_only_after_central_authorization_and_retry_reuses_local_collection(self):
        binding = self.start()
        cash.start_attention(binding.pk)
        self.assertEqual(CashCollectionSession.objects.count(), 1)
        self.assertTrue(get_cash_acceptor().snapshot().accepting)
        self.assertEqual(binding.collection.remote_session_id, str(binding.pk))

    def test_offline_start_does_not_enable_or_create_collection(self):
        self.central.offline = True
        with self.assertRaises(CashSyncError):
            self.start()
        self.assertFalse(CashCollectionSession.objects.exists())
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_currency_mismatch_prevents_enabling(self):
        self.central.currency = "EUR"
        with self.assertRaises(CashSyncError):
            self.start()
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_missing_local_journal_with_existing_central_funds_is_blocked(self):
        self.central.credits[str(uuid4())] = 500
        with self.assertRaisesMessage(CashSyncError, "diario local"):
            self.start()
        self.assertFalse(AttentionBinding.objects.exists())

    def test_bill_is_durable_and_sync_uses_stable_note_uuid(self):
        binding = self.start()
        note = self.note(binding)
        event = OutboxEvent.objects.get(topic="cash.note.credited")
        self.assertIsNone(event.delivered_at)
        cash.worker_cycle()
        event.refresh_from_db()
        self.assertIsNotNone(event.delivered_at)
        self.assertEqual(self.central.credits, {str(note.pk): 500})
        self.assertEqual(cash.snapshot(AttentionBinding.objects.get())['pending_cash_events'], 0)

    def test_lost_central_reply_retries_same_event_without_duplicate_credit(self):
        binding = self.start()
        note = self.note(binding)
        self.central.lose_reply = True
        with self.assertRaises(CashSyncError):
            cash.worker_cycle()
        self.assertFalse(get_cash_acceptor().snapshot().accepting)
        event = OutboxEvent.objects.get(topic="cash.note.credited")
        self.assertIsNone(event.delivered_at)
        binding.refresh_from_db()
        cash.sync_notes(binding, force=True)
        self.assertEqual(self.central.credits, {str(note.pk): 500})
        event.refresh_from_db()
        self.assertIsNotNone(event.delivered_at)
        self.assertEqual(event.attempts, 2)

    def test_network_loss_inhibits_preserves_and_retries_after_restart(self):
        binding = self.start()
        self.note(binding)
        self.central.offline = True
        with self.assertRaises(CashSyncError):
            cash.worker_cycle()
        self.assertFalse(get_cash_acceptor().snapshot().accepting)
        reset_cash_acceptor()
        self.central.offline = False
        cash.initialize_hardware()
        cash.sync_notes(AttentionBinding.objects.get(), force=True)
        state = cash.stop_attention(binding.pk)
        self.assertTrue(state["sealed"])
        self.assertEqual(state["central"]["balance_minor"], 500)
        self.assertEqual(AcceptedBanknote.objects.count(), 1)

    def test_connection_is_checked_without_waiting_for_next_banknote(self):
        binding = self.start()
        self.central.offline = True
        with self.assertRaises(CashSyncError):
            cash.worker_cycle()
        self.assertFalse(get_cash_acceptor().snapshot().accepting)
        self.assertFalse(AcceptedBanknote.objects.exists())
        binding.refresh_from_db()
        self.assertEqual(binding.last_error, "central_unavailable")

    def test_revoked_station_is_inhibited_without_discarding_durable_note(self):
        binding = self.start()
        self.note(binding)
        self.central.revoked = True
        with self.assertRaises(CashSyncError):
            cash.worker_cycle()
        self.assertEqual(sum(self.central.credits.values()), 500)
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_heartbeat_expiration_stops_reception_and_still_syncs(self):
        binding = self.start()
        self.note(binding)
        AttentionBinding.objects.filter(pk=binding.pk).update(heartbeat_at=timezone.now() - timedelta(seconds=21))
        cash.worker_cycle()
        self.assertFalse(get_cash_acceptor().snapshot().accepting)
        self.assertEqual(sum(self.central.credits.values()), 500)
        self.assertFalse(AttentionBinding.objects.get().sealed)

    def test_stopped_collection_never_resumes_on_start_retry(self):
        binding = self.start()
        cash.inhibit(binding)
        with self.assertRaises(InvalidCollectionState):
            cash.start_attention(binding.pk)
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_stop_seals_only_after_all_notes_confirmed_and_is_idempotent(self):
        binding = self.start()
        self.note(binding)
        first = cash.stop_attention(binding.pk)
        again = cash.stop_attention(binding.pk)
        self.assertTrue(first["sealed"])
        self.assertEqual(first["central"]["received_minor"], 500)
        self.assertEqual(again["central"]["event_count"], 1)
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_offline_stop_does_not_seal_or_delete_money(self):
        binding = self.start()
        self.note(binding)
        self.central.offline = True
        with self.assertRaises(CashSyncError):
            cash.stop_attention(binding.pk)
        self.assertFalse(AttentionBinding.objects.get().sealed)
        self.assertEqual(AcceptedBanknote.objects.count(), 1)
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_empty_attention_can_be_stopped_even_when_local_start_never_happened(self):
        state = cash.stop_attention(self.central.attention_id)
        self.assertTrue(state["sealed"])
        self.assertEqual(state["cash_received_local_minor"], 0)
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_another_pending_attention_blocks_new_local_collection(self):
        self.start()
        self.central.attention_id = uuid4()
        with self.assertRaises(CashSyncError):
            self.start()
        self.assertEqual(CashCollectionSession.objects.count(), 1)

    def test_initialization_disables_physical_device_before_querying_database(self):
        self.start()
        with patch("apps.station_cash.services.AttentionBinding.objects.filter", side_effect=DatabaseError("disk error")):
            with self.assertRaises(DatabaseError):
                cash.initialize_hardware()
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_storage_failure_while_stopping_latches_fault_and_disables(self):
        binding = self.start()
        with patch.object(CashCollectionSession, "save", side_effect=DatabaseError("disk error")):
            with self.assertRaises(DatabaseError):
                stop_collection(binding.collection_id, reason="device_error")
        self.assertTrue(get_cash_acceptor_poller().faulted)
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_existing_device_identity_cannot_be_reused_with_another_amount(self):
        binding = self.start()
        event = get_cash_acceptor().simulate_note(currency="USD", denomination_minor=500)
        record_note_credit(collection_id=binding.collection_id, note_event=event)
        from dataclasses import replace
        with self.assertRaises(IdempotencyConflict):
            record_note_credit(collection_id=binding.collection_id, note_event=replace(event, denomination_minor=100))
        self.assertEqual(AcceptedBanknote.objects.count(), 1)

    def test_api_local_key_origin_and_legacy_bypass_are_checked(self):
        route = "/api/v1/station/attentions/start/"
        data = {"attention_id": str(self.central.attention_id)}
        self.client.credentials()
        self.assertEqual(self.client.post(route, data, format="json").status_code, 403)
        self.client.credentials(HTTP_X_LOCAL_API_KEY=settings.LOCAL_API_KEY, HTTP_ORIGIN="https://evil.test")
        self.assertEqual(self.client.post(route, data, format="json").status_code, 403)
        self.client.credentials(HTTP_X_LOCAL_API_KEY=settings.LOCAL_API_KEY, HTTP_ORIGIN="https://kiosk.example.test")
        response = self.client.post(route, data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("CENTRAL_AGENT_CREDENTIAL", str(response.data))
        self.assertEqual(self.client.post("/api/v1/cash/collections/", {}, format="json").status_code, 409)
        self.assertEqual(self.client.get(f'/api/v1/station/attentions/{self.central.attention_id}/')["Cache-Control"], "no-store")
