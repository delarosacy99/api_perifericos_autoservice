from django.db import migrations, models

TOPICS = ("door.front.snapshot", "door.front.opened", "door.front.closed", "door.front.disconnected", "door.front.recovered", "door.front.unknown")


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Event = apps.get_model("outbox", "OutboxEvent")
    State = apps.get_model("door_monitor", "DoorMonitorState")
    query = Event.objects.using(alias).filter(aggregate_type="door", aggregate_id="front", topic__in=TOPICS).order_by("occurred_at", "id")
    count = 0
    for event in query.iterator():
        count += 1
        event.payload = {**event.payload, "sequence": count}
        event.save(using=alias, update_fields=["payload"])
    if count:
        state, _ = State.objects.using(alias).get_or_create(pk="front", defaults={"door_name": "Puerta frontal", "coverage": "front_door_only"})
        state.event_sequence = count
        state.save(using=alias, update_fields=["event_sequence"])


class Migration(migrations.Migration):
    dependencies = [("door_monitor", "0001_initial"), ("outbox", "0001_initial")]
    operations = [
        migrations.AddField(model_name="doormonitorstate", name="event_sequence", field=models.PositiveBigIntegerField(default=0)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
