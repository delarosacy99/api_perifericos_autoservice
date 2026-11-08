import json

from django.core.management.base import BaseCommand, CommandError

from apps.outbox.models import OutboxEvent


class Command(BaseCommand):
    help = "Muestra los eventos persistidos del monitor de puerta frontal."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Cantidad máxima de eventos recientes (1 a 100).",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 100:
            raise CommandError("--limit debe estar entre 1 y 100.")

        events = list(
            OutboxEvent.objects.filter(topic__startswith="door.").order_by("-occurred_at")[:limit]
        )
        events.reverse()
        payload = [
            {
                "event_id": str(event.id),
                "topic": event.topic,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "payload": event.payload,
                "occurred_at": event.occurred_at,
                "delivered_at": event.delivered_at,
                "attempts": event.attempts,
                "last_error": event.last_error,
                "retry_at": event.retry_at,
            }
            for event in events
        ]
        self.stdout.write(
            json.dumps({"count": len(payload), "events": payload}, indent=2, default=str)
        )
