import json
from datetime import UTC, datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.bridge.runtime import acquire_process_lock
from apps.cash_acceptor.drivers.journal import HardwareJournal
from apps.cash_acceptor.models import AcceptedBanknote


class Command(BaseCommand):
    help = (
        "Resolución técnica de ACK incierto. Detenga el agente y concilie los contadores físicos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--decision", choices=["ack-applied", "ack-not-applied"], required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--confirm-reconciled", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm_reconciled"] or len(options["reason"].strip()) < 10:
            raise CommandError("Confirme la conciliación y describa la evidencia observada.")
        try:
            acquire_process_lock()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from None
        journal = HardwareJournal(settings.BASE_DIR / "var" / "nv200-ack.json")
        data = journal.read()
        if not data or data["stage"] != "ack_intent":
            raise CommandError("No hay un ACK incierto que resolver.")
        for note in data["notes"]:
            if not AcceptedBanknote.objects.filter(
                device_event_id=note["event_id"],
                denomination_minor=note["denomination_minor"],
                currency=note["currency"],
            ).exists():
                raise CommandError(
                    "El billete no está respaldado por el diario local. No se modificó el ACK."
                )
        audit_path = journal.path.with_name("nv200-ack-resolutions.jsonl")
        audit = {
            "at": datetime.now(UTC).isoformat(),
            "decision": options["decision"],
            "reason": options["reason"],
            "previous": data,
        }
        with audit_path.open("a", encoding="utf-8") as stream:
            import os

            stream.write(json.dumps(audit) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        data["stage"] = "acked" if options["decision"] == "ack-applied" else "pending"
        journal.write(data)
        self.stdout.write(
            self.style.SUCCESS(
                "Resolución registrada. Reinicie el agente y verifique la misma atención."
            )
        )
