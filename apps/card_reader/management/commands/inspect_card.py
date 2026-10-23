import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError

from ...drivers import get_card_reader
from ...drivers.base import CardReaderError


class Command(BaseCommand):
    help = "Inspecciona una tarjeta ISO14443-4/DESFire sin modificarla."

    def handle(self, *args, **options):
        try:
            inspection = get_card_reader().inspect_card()
        except CardReaderError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(asdict(inspection), indent=2, ensure_ascii=False))
