import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError

from ...drivers import get_card_reader
from ...drivers.base import CardReaderError


class Command(BaseCommand):
    help = "Valida la seguridad PICC y la clave de fábrica sin escribir en la tarjeta."

    def handle(self, *args, **options):
        try:
            probe = get_card_reader().probe_security()
        except CardReaderError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(asdict(probe), indent=2, ensure_ascii=False))
