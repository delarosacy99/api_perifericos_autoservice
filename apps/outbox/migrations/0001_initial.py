import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="OutboxEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("topic", models.CharField(db_index=True, max_length=100)),
                ("aggregate_type", models.CharField(max_length=50)),
                ("aggregate_id", models.CharField(db_index=True, max_length=64)),
                ("payload", models.JSONField(default=dict)),
                ("occurred_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("delivered_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True)),
            ],
            options={"ordering": ["occurred_at", "id"]},
        ),
        migrations.AddIndex(
            model_name="outboxevent",
            index=models.Index(fields=["delivered_at", "occurred_at"], name="outbox_pending_idx"),
        ),
    ]
