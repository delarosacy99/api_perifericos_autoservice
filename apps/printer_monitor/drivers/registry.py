from functools import lru_cache

from django.conf import settings

from .base import PrinterMonitorConfigurationError, PrinterMonitorDriver
from .ceprintlib import CustomK80CePrintLibConfig, CustomK80CePrintLibDriver
from .cesmlm import CustomK80CeSmLmConfig, CustomK80CeSmLmDriver
from .simulator import SimulatedPrinterMonitorDriver


@lru_cache(maxsize=1)
def get_printer_monitor() -> PrinterMonitorDriver:
    if settings.PRINTER_MONITOR_DRIVER == "simulator":
        return SimulatedPrinterMonitorDriver(
            raw_status=settings.PRINTER_SIMULATOR_RAW_STATUS,
            paper_remaining_cm=settings.PRINTER_SIMULATOR_PAPER_REMAINING_CM,
            mapping_physically_validated=settings.CUSTOM_K80_STATUS_MAPPING_VALIDATED,
        )
    if settings.PRINTER_MONITOR_DRIVER == "custom_k80_cesmlm":
        return CustomK80CeSmLmDriver(
            CustomK80CeSmLmConfig(
                printer_name=settings.CUSTOM_K80_PRINTER_NAME,
                dll_path=settings.CUSTOM_K80_CESMLM_DLL_PATH,
                query_remaining_paper=settings.CUSTOM_K80_QUERY_REMAINING_PAPER,
                mapping_physically_validated=(
                    settings.CUSTOM_K80_STATUS_MAPPING_VALIDATED
                ),
            )
        )
    if settings.PRINTER_MONITOR_DRIVER == "custom_k80_ceprintlib":
        return CustomK80CePrintLibDriver(
            CustomK80CePrintLibConfig(
                printer_name=settings.CUSTOM_K80_PRINTER_NAME,
                dll_path=settings.CUSTOM_K80_CEPRNLIB_DLL_PATH,
                mapping_physically_validated=(
                    settings.CUSTOM_K80_STATUS_MAPPING_VALIDATED
                ),
            )
        )
    raise PrinterMonitorConfigurationError(
        f"PRINTER_MONITOR_DRIVER={settings.PRINTER_MONITOR_DRIVER!r} no está implementado."
    )


def reset_printer_monitor() -> None:
    if get_printer_monitor.cache_info().currsize:
        get_printer_monitor().disconnect()
    get_printer_monitor.cache_clear()

