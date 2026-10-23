from functools import lru_cache

from django.conf import settings

from .base import DoorMonitorConfigurationError, DoorMonitorDriver
from .simulator import SimulatedDoorMonitorDriver
from .xl128 import XL128SerialConfig, XL128SerialDoorMonitor


@lru_cache(maxsize=1)
def get_door_monitor() -> DoorMonitorDriver:
    if settings.DOOR_MONITOR_DRIVER == "simulator":
        return SimulatedDoorMonitorDriver(
            is_open=settings.DOOR_SIMULATOR_STATE == "open",
        )
    if settings.DOOR_MONITOR_DRIVER == "xl128_serial":
        if not settings.XL128_COM_PORT:
            raise DoorMonitorConfigurationError(
                "XL128_COM_PORT es obligatorio cuando DOOR_MONITOR_DRIVER=xl128_serial."
            )
        return XL128SerialDoorMonitor(
            XL128SerialConfig(
                port=settings.XL128_COM_PORT,
                baud_rate=settings.XL128_BAUD_RATE,
                timeout_seconds=settings.XL128_COMMAND_TIMEOUT_SECONDS,
                write_timeout_seconds=settings.XL128_WRITE_TIMEOUT_SECONDS,
                attempts=settings.XL128_COMMAND_ATTEMPTS,
            ),
            open_codes=tuple(settings.XL128_DOOR_OPEN_RESPONSE_CODES),
            closed_codes=tuple(settings.XL128_DOOR_CLOSED_RESPONSE_CODES),
        )
    raise DoorMonitorConfigurationError(
        f"DOOR_MONITOR_DRIVER={settings.DOOR_MONITOR_DRIVER!r} no está implementado."
    )


def reset_door_monitor() -> None:
    if get_door_monitor.cache_info().currsize:
        get_door_monitor().disconnect()
    get_door_monitor.cache_clear()
