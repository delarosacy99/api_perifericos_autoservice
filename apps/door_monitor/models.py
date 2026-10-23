from django.db import models


class DoorMonitorState(models.Model):
    class State(models.TextChoices):
        UNKNOWN = "unknown", "Desconocido"
        OPEN = "open", "Abierta"
        CLOSED = "closed", "Cerrada"

    door_id = models.CharField(max_length=32, primary_key=True)
    door_name = models.CharField(max_length=100)
    coverage = models.CharField(max_length=50)
    connected = models.BooleanField(default=False)
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.UNKNOWN,
    )
    raw_status_hex = models.CharField(max_length=2, blank=True)
    last_observed_at = models.DateTimeField(null=True, blank=True)
    state_changed_at = models.DateTimeField(null=True, blank=True)
    connection_changed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["door_id"]

    def __str__(self) -> str:
        return f"{self.door_id}:{self.state}:{'online' if self.connected else 'offline'}"
