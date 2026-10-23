import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError

from ...drivers import get_card_reader
from ...drivers.base import CardReaderError
from ._provisioning import get_provisioning_master_key


class Command(BaseCommand):
    help = "Autentica y lee una credencial DESFire aprovisionada, sin escribir en la tarjeta."

    def handle(self, *args, **options):
        try:
            result = get_card_reader().verify_credential(
                master_key=get_provisioning_master_key(),
            )
        except (CardReaderError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(asdict(result), indent=2, ensure_ascii=False))
