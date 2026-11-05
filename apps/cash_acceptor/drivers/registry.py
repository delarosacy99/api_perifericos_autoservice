from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import CashAcceptorDriver
from .nv200 import NV200Config, NV200ESSPDriver
from .simulator import SimulatedNV200Driver


@lru_cache(maxsize=1)
def get_cash_acceptor() -> CashAcceptorDriver:
    if settings.CASH_ACCEPTOR_DRIVER == "simulator":
        return SimulatedNV200Driver()
    if settings.CASH_ACCEPTOR_DRIVER == "nv200_essp":
        if not settings.NV200_COM_PORT:
            raise ImproperlyConfigured(
                "NV200_COM_PORT es obligatorio cuando CASH_ACCEPTOR_DRIVER=nv200_essp."
            )
        return NV200ESSPDriver(
            NV200Config(
                port=settings.NV200_COM_PORT,
                baud_rate=settings.NV200_BAUD_RATE,
                address=settings.NV200_SSP_ADDRESS,
                timeout_seconds=settings.NV200_COMMAND_TIMEOUT_SECONDS,
                write_timeout_seconds=settings.NV200_WRITE_TIMEOUT_SECONDS,
                attempts=settings.NV200_COMMAND_ATTEMPTS,
                minimum_protocol_version=settings.NV200_MIN_PROTOCOL_VERSION,
                maximum_protocol_version=settings.NV200_MAX_PROTOCOL_VERSION,
                fixed_key=settings.NV200_FIXED_KEY,
                poll_with_ack=settings.NV200_POLL_WITH_ACK,
                journal_path=str(settings.BASE_DIR / "var" / "nv200-ack.json") if settings.CENTRAL_SYNC_ENABLED else "",
            )
        )
    raise ImproperlyConfigured(
        f"CASH_ACCEPTOR_DRIVER={settings.CASH_ACCEPTOR_DRIVER!r} todavía no está implementado."
    )


def reset_cash_acceptor() -> None:
    from ..runtime import reset_cash_acceptor_poller

    reset_cash_acceptor_poller()
    if get_cash_acceptor.cache_info().currsize:
        get_cash_acceptor().disconnect()
    get_cash_acceptor.cache_clear()
