from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("outbox", "0001_initial")]
    operations = [migrations.AddField(model_name="outboxevent", name="retry_at", field=models.DateTimeField(null=True, blank=True))]
