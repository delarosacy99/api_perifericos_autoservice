import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...drivers import get_printer_monitor
from ...drivers.base import PrinterMonitorError
from ...services import serialize_observation


class Command(BaseCommand):
    help = "Consulta el estado de la CUSTOM K80 sin imprimir, alimentar ni cortar papel."

    def add_arguments(self, parser):
        parser.add_argument(
            "--samples",
            type=int,
            default=3,
            help="Cantidad de consultas de solo lectura (1 a 100).",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=0.5,
            help="Segundos entre consultas; el SDK recomienda al menos 0.5.",
        )

    def handle(self, *args, **options):
        samples = options["samples"]
        interval = options["interval"]
        if not 1 <= samples <= 100:
            raise CommandError("--samples debe estar entre 1 y 100.")
        if not 0 <= interval <= 60:
            raise CommandError("--interval debe estar entre 0 y 60 segundos.")
        if samples > 1 and interval < 0.5:
            raise CommandError(
                "--interval debe ser al menos 0.5 segundos cuando se toman varias muestras."
            )

        observations = []
        try:
            monitor = get_printer_monitor()
            for index in range(samples):
                observations.append(serialize_observation(monitor.read_status()))
                if index + 1 < samples:
                    time.sleep(interval)
        except PrinterMonitorError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            json.dumps(
                {
                    "mode": "read_only",
                    "printer_name": settings.CUSTOM_K80_PRINTER_NAME,
                    "full_status_query_hex": "10 04 14",
                    "actions_excluded": ["print", "paper_feed", "cut", "reset"],
                    "samples": observations,
                },
                indent=2,
                default=str,
            )
        )

