from django.db import migrations, models


def map_legacy_session_states(apps, schema_editor):
    Collection = apps.get_model("cash_acceptor", "CashCollectionSession")

    for collection in Collection.objects.filter(status="completed").iterator():
        collection.status = "stopped"
        collection.stop_reason = "remote_completed"
        collection.stopped_at = collection.completed_at
        collection.save(update_fields=["status", "stop_reason", "stopped_at"])

    for collection in Collection.objects.filter(status="cancelled").iterator():
        collection.status = "stopped"
        collection.stop_reason = "user_cancelled"
        collection.stopped_at = collection.cancelled_at
        collection.save(update_fields=["status", "stop_reason", "stopped_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("cash_acceptor", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="PaymentSession",
            new_name="CashCollectionSession",
        ),
        migrations.RenameModel(
            old_name="CashCredit",
            new_name="AcceptedBanknote",
        ),
        migrations.RenameField(
            model_name="cashcollectionsession",
            old_name="external_reference",
            new_name="remote_session_id",
        ),
        migrations.RenameField(
            model_name="cashcollectionsession",
            old_name="credited_amount_minor",
            new_name="accepted_amount_minor",
        ),
        migrations.RenameField(
            model_name="acceptedbanknote",
            old_name="credited_at",
            new_name="accepted_at",
        ),
        migrations.AddField(
            model_name="cashcollectionsession",
            name="stopped_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cashcollectionsession",
            name="stop_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("remote_completed", "El servidor completó la operación"),
                    ("user_cancelled", "El usuario canceló"),
                    ("frontend_disconnected", "El frontend se desconectó"),
                    ("device_error", "Error del periférico"),
                    ("service_shutdown", "El servicio se está deteniendo"),
                    ("operator_requested", "Detenida por un operador"),
                ],
                max_length=64,
            ),
        ),
        migrations.RunPython(map_legacy_session_states, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="cashcollectionsession",
            name="requested_amount_minor",
        ),
        migrations.RemoveField(
            model_name="cashcollectionsession",
            name="consumed_amount_minor",
        ),
        migrations.RemoveField(
            model_name="cashcollectionsession",
            name="credit_balance_minor",
        ),
        migrations.RemoveField(
            model_name="cashcollectionsession",
            name="completed_at",
        ),
        migrations.RemoveField(
            model_name="cashcollectionsession",
            name="cancelled_at",
        ),
        migrations.AlterField(
            model_name="cashcollectionsession",
            name="remote_session_id",
            field=models.CharField(db_index=True, max_length=128),
        ),
        migrations.AlterField(
            model_name="cashcollectionsession",
            name="status",
            field=models.CharField(
                choices=[
                    ("created", "Creada"),
                    ("accepting", "Aceptando efectivo"),
                    ("stopped", "Detenida"),
                    ("interrupted", "Interrumpida"),
                ],
                db_index=True,
                default="created",
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name="acceptedbanknote",
            options={"ordering": ["accepted_at", "id"]},
        ),
        migrations.AlterField(
            model_name="acceptedbanknote",
            name="session",
            field=models.ForeignKey(
                on_delete=models.PROTECT,
                related_name="accepted_banknotes",
                to="cash_acceptor.cashcollectionsession",
            ),
        ),
        migrations.RemoveIndex(
            model_name="acceptedbanknote",
            name="cash_credit_session_idx",
        ),
        migrations.AddIndex(
            model_name="acceptedbanknote",
            index=models.Index(
                fields=["session", "accepted_at"],
                name="accepted_note_session_idx",
            ),
        ),
    ]
