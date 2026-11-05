from django.core.management.base import BaseCommand

from apps.bridge.central import central
from apps.bridge.services import snapshot


class Command(BaseCommand):
    help = "Estado local del puente, sin claves; --probe valida bootstrap central."

    def add_arguments(self, parser):
        parser.add_argument("--probe", action="store_true")

    def handle(self, *args, **options):
        import json

        data = snapshot()
        if options["probe"]:
            from django.core.management.base import CommandError

            from apps.bridge.errors import BridgeError

            try:
                data["central_configuration"] = central.bootstrap()
            except BridgeError as exc:
                raise CommandError(f"Conexión central pendiente: {exc.code}") from None
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
