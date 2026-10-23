from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import CardReaderDriver
from .crt603 import CRT603SerialDriver
from .crt603_protocol import CRT603SerialConfig
from .simulator import SimulatedCRT603Driver


@lru_cache(maxsize=1)
def get_card_reader() -> CardReaderDriver:
    if settings.CARD_READER_DRIVER == "simulator":
        return SimulatedCRT603Driver(
            credential_application_id=settings.CARD_CREDENTIAL_APPLICATION_ID,
        )
    if settings.CARD_READER_DRIVER == "crt603_serial":
        if not settings.CRT603_COM_PORT:
            raise ImproperlyConfigured(
                "CRT603_COM_PORT es obligatorio cuando CARD_READER_DRIVER=crt603_serial."
            )
        return CRT603SerialDriver(
            CRT603SerialConfig(
                port=settings.CRT603_COM_PORT,
                baud_rate=settings.CRT603_BAUD_RATE,
                timeout_seconds=settings.CRT603_COMMAND_TIMEOUT_SECONDS,
                write_timeout_seconds=settings.CRT603_WRITE_TIMEOUT_SECONDS,
                attempts=settings.CRT603_COMMAND_ATTEMPTS,
            ),
            credential_application_id=settings.CARD_CREDENTIAL_APPLICATION_ID,
        )
    raise ImproperlyConfigured(
        f"CARD_READER_DRIVER={settings.CARD_READER_DRIVER!r} no está implementado."
    )


def reset_card_reader() -> None:
    if get_card_reader.cache_info().currsize:
        get_card_reader().disconnect()
    get_card_reader.cache_clear()
