import uuid

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.outbox.models import OutboxEvent

from ..drivers import get_cash_acceptor, reset_cash_acceptor
from ..drivers.base import NoteCreditEvent
from ..models import AcceptedBanknote, CashCollectionSession
from ..services import record_note_credit


@override_settings(
    CASH_ACCEPTOR_DRIVER="simulator",
    CASH_CURRENCY="USD",
    CASH_EXPECTED_DATASET_CURRENCY="USD",
    CASH_SIMULATOR_DATASET_CURRENCY="USD",
    CASH_ACCEPT_ALL_DATASET_DENOMINATIONS=True,
    CASH_ACCEPTED_DENOMINATIONS_MINOR=(100, 200, 500, 1_000, 2_000, 5_000, 10_000),
)
class CashAPITestCase(APITestCase):
    def setUp(self) -> None:
        reset_cash_acceptor()
        self.client.credentials(HTTP_X_LOCAL_API_KEY=settings.LOCAL_API_KEY)

    def tearDown(self) -> None:
        reset_cash_acceptor()

    def create_collection(
        self,
        *,
        remote_session_id: str | None = None,
        idempotency_key: str | None = None,
        extra: dict | None = None,
    ):
        payload = {
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
            "remote_session_id": remote_session_id or f"remote-{uuid.uuid4()}",
            "currency": "USD",
        }
        payload.update(extra or {})
        return self.client.post(reverse("cash-collection-list"), payload, format="json")

    def start_collection(self, collection_id: str):
        return self.client.post(
            reverse("cash-collection-start", args=[collection_id]),
            {},
            format="json",
        )

    def stop_collection(self, collection_id: str, reason: str = "remote_completed"):
        return self.client.post(
            reverse("cash-collection-stop", args=[collection_id]),
            {"reason": reason},
            format="json",
        )

    def simulate_note(self, collection_id: str, denomination_minor: int):
        return self.client.post(
            reverse("cash-acceptor-simulate-note"),
            {
                "collection_session_id": collection_id,
                "denomination_minor": denomination_minor,
            },
            format="json",
        )

    def test_endpoints_require_local_api_key(self):
        self.client.credentials()
        response = self.client.get(reverse("station-configuration"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_station_configuration_declares_no_business_or_financial_rules(self):
        response = self.client.get(reverse("station-configuration"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["cash"]["currency"], "USD")
        self.assertTrue(response.data["cash"]["accept_all_dataset_denominations"])
        self.assertTrue(response.data["capabilities"]["cash_collection"])
        self.assertFalse(response.data["capabilities"]["business_rules"])
        self.assertFalse(response.data["capabilities"]["financial_ledger"])

    def test_collection_creation_is_idempotent(self):
        idempotency_key = str(uuid.uuid4())
        remote_session_id = "remote-operation-001"
        first = self.create_collection(
            idempotency_key=idempotency_key,
            remote_session_id=remote_session_id,
        )
        second = self.create_collection(
            idempotency_key=idempotency_key,
            remote_session_id=remote_session_id,
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(CashCollectionSession.objects.count(), 1)

    def test_reusing_idempotency_key_for_another_remote_session_is_rejected(self):
        idempotency_key = str(uuid.uuid4())
        self.create_collection(
            idempotency_key=idempotency_key,
            remote_session_id="remote-operation-001",
        )
        response = self.create_collection(
            idempotency_key=idempotency_key,
            remote_session_id="remote-operation-002",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "idempotency_conflict")

    def test_business_rule_fields_are_rejected(self):
        response = self.create_collection(
            extra={
                "requested_amount_minor": 1_500,
                "credit_balance_minor": 500,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("requested_amount_minor", response.data)
        self.assertIn("credit_balance_minor", response.data)

    def test_collection_response_has_no_business_amount_fields(self):
        response = self.create_collection()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("accepted_amount_minor", response.data)
        self.assertNotIn("requested_amount_minor", response.data)
        self.assertNotIn("consumed_amount_minor", response.data)
        self.assertNotIn("credit_balance_minor", response.data)

    def test_all_usd_dataset_denominations_are_enabled_and_accepted(self):
        created = self.create_collection()
        collection_id = created.data["id"]
        started = self.start_collection(collection_id)

        self.assertEqual(started.status_code, status.HTTP_200_OK)
        status_response = self.client.get(reverse("cash-acceptor-status"))
        enabled = {
            channel["denomination_minor"]
            for channel in status_response.data["channels"]
            if channel["enabled"]
        }
        self.assertEqual(enabled, {100, 200, 500, 1_000, 2_000, 5_000, 10_000})

        for denomination in sorted(enabled):
            response = self.simulate_note(collection_id, denomination)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        collection = CashCollectionSession.objects.get(pk=collection_id)
        self.assertEqual(collection.accepted_amount_minor, 18_800)
        self.assertEqual(collection.accepted_banknotes.count(), 7)

    def test_cash_acceptor_status_exposes_cashbox_state(self):
        response = self.client.get(reverse("cash-acceptor-status"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["cashbox_present"])
        self.assertEqual(response.data["alerts"], [])

    def test_stop_only_records_hardware_total_and_reason(self):
        created = self.create_collection()
        collection_id = created.data["id"]
        self.start_collection(collection_id)
        self.simulate_note(collection_id, 2_000)

        with self.captureOnCommitCallbacks(execute=True):
            stopped = self.stop_collection(collection_id, "user_cancelled")

        self.assertEqual(stopped.status_code, status.HTTP_200_OK)
        self.assertEqual(stopped.data["status"], CashCollectionSession.Status.STOPPED)
        self.assertEqual(stopped.data["stop_reason"], "user_cancelled")
        self.assertEqual(stopped.data["accepted_amount_minor"], 2_000)
        self.assertNotIn("credit_balance_minor", stopped.data)
        self.assertNotIn("consumed_amount_minor", stopped.data)
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_collection_can_stop_with_zero_or_any_accepted_amount(self):
        created = self.create_collection()
        stopped = self.stop_collection(created.data["id"], "operator_requested")

        self.assertEqual(stopped.status_code, status.HTTP_200_OK)
        self.assertEqual(stopped.data["accepted_amount_minor"], 0)
        self.assertEqual(stopped.data["status"], CashCollectionSession.Status.STOPPED)

    def test_only_one_collection_can_control_cash_acceptor(self):
        first = self.create_collection()
        second = self.create_collection()
        self.start_collection(first.data["id"])

        response = self.start_collection(second.data["id"])

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "active_collection_exists")

    def test_unsupported_note_is_rejected_without_acceptance_record(self):
        created = self.create_collection()
        collection_id = created.data["id"]
        self.start_collection(collection_id)

        response = self.simulate_note(collection_id, 300)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "unsupported_denomination")
        self.assertEqual(AcceptedBanknote.objects.count(), 0)

    def test_duplicate_device_credit_event_is_recorded_only_once(self):
        created = self.create_collection()
        collection_id = created.data["id"]
        self.start_collection(collection_id)
        note_event = NoteCreditEvent(
            event_id="nv200-credit-0001",
            channel=4,
            currency="USD",
            denomination_minor=1_000,
            raw_event={"event": "NOTE_CREDIT", "channel": 4},
        )

        _, first_banknote, first_created = record_note_credit(
            collection_id=collection_id,
            note_event=note_event,
        )
        collection, second_banknote, second_created = record_note_credit(
            collection_id=collection_id,
            note_event=note_event,
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_banknote.id, second_banknote.id)
        self.assertEqual(collection.accepted_amount_minor, 1_000)
        self.assertEqual(AcceptedBanknote.objects.count(), 1)

    def test_stopping_created_collection_does_not_disable_active_collection(self):
        active = self.create_collection()
        inactive = self.create_collection()
        self.start_collection(active.data["id"])

        stopped = self.stop_collection(inactive.data["id"], "operator_requested")

        self.assertEqual(stopped.status_code, status.HTTP_200_OK)
        self.assertTrue(get_cash_acceptor().snapshot().accepting)

    @override_settings(
        CASH_EXPECTED_DATASET_CURRENCY="DOP",
        CASH_SIMULATOR_DATASET_CURRENCY="USD",
    )
    def test_dataset_currency_mismatch_keeps_validator_disabled(self):
        reset_cash_acceptor()
        created = self.create_collection()
        response = self.start_collection(created.data["id"])

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "dataset_currency_mismatch")
        self.assertFalse(get_cash_acceptor().snapshot().accepting)

    def test_cash_operations_write_hardware_only_outbox_events(self):
        created = self.create_collection(remote_session_id="remote-operation-001")
        collection_id = created.data["id"]
        self.start_collection(collection_id)
        self.simulate_note(collection_id, 1_000)
        with self.captureOnCommitCallbacks(execute=True):
            self.stop_collection(collection_id, "remote_completed")

        events = list(OutboxEvent.objects.order_by("occurred_at"))
        self.assertEqual(
            [event.topic for event in events],
            [
                "cash.collection.created",
                "cash.collection.started",
                "cash.note.credited",
                "cash.collection.stopped",
            ],
        )
        for event in events:
            self.assertEqual(event.payload["remote_session_id"], "remote-operation-001")
            self.assertNotIn("requested_amount_minor", event.payload)
            self.assertNotIn("consumed_amount_minor", event.payload)
            self.assertNotIn("credit_balance_minor", event.payload)
