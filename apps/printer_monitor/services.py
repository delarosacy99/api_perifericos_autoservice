from dataclasses import asdict

from django.utils import timezone

from .drivers.base import PrinterObservation


def serialize_observation(observation: PrinterObservation) -> dict:
    payload = asdict(observation)
    payload["alerts"] = list(observation.alerts)
    payload["observed_at"] = timezone.now()
    return payload

