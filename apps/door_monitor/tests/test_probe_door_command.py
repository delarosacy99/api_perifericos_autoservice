import json
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from ..drivers import reset_door_monitor


@override_settings(DOOR_MONITOR_DRIVER="simulator", DOOR_SIMULATOR_STATE="closed")
class ProbeDoorCommandTestCase(SimpleTestCase):
    def setUp(self):
        reset_door_monitor()

    def tearDown(self):
        reset_door_monitor()

    def test_probe_is_read_only_and_returns_requested_samples(self):
        output = StringIO()

        call_command("probe_door", samples=2, interval=0, stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "read_only")
        self.assertEqual(payload["query_command_hex"], "1B70")
        self.assertEqual(len(payload["samples"]), 2)
        self.assertTrue(all(item["monitoring_only"] for item in payload["samples"]))

    def test_probe_rejects_unbounded_sample_count(self):
        with self.assertRaisesMessage(CommandError, "--samples"):
            call_command("probe_door", samples=101, interval=0)
