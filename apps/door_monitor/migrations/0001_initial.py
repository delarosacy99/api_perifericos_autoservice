from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DoorMonitorState",
            fields=[
                ("door_id", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("door_name", models.CharField(max_length=100)),
                ("coverage", models.CharField(max_length=50)),
                ("connected", models.BooleanField(default=False)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("unknown", "Desconocido"),
                            ("open", "Abierta"),
                            ("closed", "Cerrada"),
                        ],
                        default="unknown",
                        max_length=16,
                    ),
                ),
                ("raw_status_hex", models.CharField(blank=True, max_length=2)),
                ("last_observed_at", models.DateTimeField(blank=True, null=True)),
                ("state_changed_at", models.DateTimeField(blank=True, null=True)),
                ("connection_changed_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["door_id"]},
        ),
    ]
