import json
from dataclasses import asdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...drivers import get_card_reader
from ...drivers.base import CardReaderError
from ._provisioning import get_provisioning_master_key


class Command(BaseCommand):
    help = (
        "Planifica o ejecuta el aprovisionamiento protegido de una tarjeta DESFire EV3 "
        "de laboratorio."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-uid",
            required=True,
            help="UID exacto de la tarjeta física autorizada.",
        )
        parser.add_argument(
            "--credential-id",
            required=True,
            help="UUID opaco que se guardará en la tarjeta; no contiene datos personales.",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Ejecuta las escrituras. Sin esta opción el comando solo muestra el plan.",
        )

    def handle(self, *args, **options):
        try:
            driver = get_card_reader()
            plan = driver.plan_provisioning(
                expected_uid=options["confirm_uid"],
                credential_id=options["credential_id"],
                write_enabled=settings.CARD_ALLOW_TEST_PROVISIONING,
            )
            if not options["execute"]:
                payload = {
                    "mode": "dry_run",
                    **asdict(plan),
                    "card_was_modified": False,
                }
                self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
                return

            if not settings.DEBUG:
                raise CommandError("El aprovisionamiento de prueba requiere DJANGO_DEBUG=true.")
            if not settings.CARD_ALLOW_TEST_PROVISIONING:
                raise CommandError(
                    "La escritura está bloqueada. Configura "
                    "CARD_ALLOW_TEST_PROVISIONING=true solamente en el laboratorio."
                )
            if not all(
                (
                    plan.will_create_aes_application,
                    plan.will_create_credential_file,
                    plan.will_personalize_keys,
                )
            ):
                raise CommandError("El plan no corresponde a una tarjeta vacía; no se modificó.")

            result = driver.provision_card(
                expected_uid=plan.uid,
                credential_id=plan.credential_id,
                master_key=get_provisioning_master_key(),
            )
        except (CardReaderError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        payload = {
            "mode": "executed",
            **asdict(result),
            "card_was_modified": True,
        }
        self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
