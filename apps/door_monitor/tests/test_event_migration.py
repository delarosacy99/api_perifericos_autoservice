from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DoorEventMigrationTests(TransactionTestCase):
    def test_existing_events_keep_their_ids_and_gain_contiguous_sequences(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [("door_monitor", "0001_initial"), ("outbox", "0001_initial")]
        try:
            executor.migrate(before)
            old_apps = executor.loader.project_state(before).apps
            Event = old_apps.get_model("outbox", "OutboxEvent")
            State = old_apps.get_model("door_monitor", "DoorMonitorState")
            State.objects.create(door_id="front", door_name="Puerta frontal", coverage="front_door_only", state="open", connected=True)
            first = Event.objects.create(topic="door.front.snapshot", aggregate_type="door", aggregate_id="front", payload={"state": "closed"})
            second = Event.objects.create(topic="door.front.opened", aggregate_type="door", aggregate_id="front", payload={"state": "open"})
            cash = Event.objects.create(topic="cash.note.credited", aggregate_type="cash_collection", aggregate_id="other", payload={"amount_minor": 500})
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from apps.outbox.models import OutboxEvent
            from apps.door_monitor.models import DoorMonitorState
            self.assertEqual(OutboxEvent.objects.get(pk=first.pk).payload["sequence"], 1)
            self.assertEqual(OutboxEvent.objects.get(pk=second.pk).payload["sequence"], 2)
            self.assertEqual(OutboxEvent.objects.get(pk=cash.pk).payload, {"amount_minor": 500})
            self.assertEqual(DoorMonitorState.objects.get(pk="front").event_sequence, 2)
            self.assertEqual(DoorMonitorState.objects.get(pk="front").state, "open")
        finally:
            MigrationExecutor(connection).migrate(latest)
