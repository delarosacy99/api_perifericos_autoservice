import io
import json

from django.core.management import call_command
from django.test import TestCase

from apps.outbox.services import enqueue_event


class ShowDoorEventsCommandTestCase(TestCase):
    def test_command_lists_only_door_events(self):
        enqueue_event(
            topic="cash.collection.started",
            aggregate_type="cash_collection",
            aggregate_id="cash-1",
            payload={},
        )
        door_event = enqueue_event(
            topic="door.front.opened",
            aggregate_type="door",
            aggregate_id="front",
            payload={"state": "open"},
        )
        output = io.StringIO()

        call_command("show_door_events", limit=20, stdout=output)

        result = json.loads(output.getvalue())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["event_id"], str(door_event.id))
        self.assertEqual(result["events"][0]["topic"], "door.front.opened")
