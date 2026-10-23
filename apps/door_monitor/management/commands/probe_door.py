import json
import time

from django.core.management.base import BaseCommand, CommandError

from ...drivers import get_door_monitor
from ...drivers.base import DoorMonitorError
from ...services import serialize_observation


class Command(BaseCommand):
    help = "Consulta el estado bruto del XL128 sin accionar la puerta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--samples",
            type=int,
            default=5,
            help="Cantidad de consultas de solo lectura (1 a 100).",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=0.5,
            help="Segundos entre consultas (0 a 60).",
        )

    def handle(self, *args, **options):
        samples = options["samples"]
        interval = options["interval"]
        if not 1 <= samples <= 100:
            raise CommandError("--samples debe estar entre 1 y 100.")
        if not 0 <= interval <= 60:
            raise CommandError("--interval debe estar entre 0 y 60 segundos.")

        observations = []
        try:
            monitor = get_door_monitor()
            for index in range(samples):
                observations.append(serialize_observation(monitor.read_status()))
                if index + 1 < samples and interval:
                    time.sleep(interval)
        except DoorMonitorError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            json.dumps(
                {
                    "mode": "read_only",
                    "query_command_hex": "1B70",
                    "samples": observations,
                },
                indent=2,
                default=str,
            )
        )
