from abc import ABC, abstractmethod
from dataclasses import dataclass


class PrinterMonitorError(Exception):
    code = "printer_monitor_error"


class PrinterMonitorConnectionError(PrinterMonitorError):
    code = "printer_monitor_connection_error"


class PrinterMonitorProtocolError(PrinterMonitorError):
    code = "printer_monitor_protocol_error"


class PrinterMonitorConfigurationError(PrinterMonitorError):
    code = "printer_monitor_configuration_error"


@dataclass(frozen=True, slots=True)
class PrinterObservation:
    device_id: str
    driver: str
    model: str
    printer_name: str
    connected: bool
    monitoring_only: bool
    state: str
    ready: bool
    alerts: tuple[str, ...]
    paper_out: bool
    paper_near_end: bool
    paper_end: bool
    virtual_paper_end: bool
    ticket_out: bool
    print_head_up: bool
    print_head_up_or_paper_jam: bool
    cover_open: bool
    paper_jam: bool
    cutter_error: bool
    over_temperature: bool
    command_error: bool
    paper_remaining_cm: int | None
    paper_remaining_estimated: bool
    paper_remaining_error: str | None
    raw_status: int
    raw_status_hex: str
    status_bytes: dict[str, str]
    interface_number: int | None
    model_gsi_hex: str | None
    mapping_source: str
    mapping_physically_validated: bool


class PrinterMonitorDriver(ABC):
    @abstractmethod
    def read_status(self) -> PrinterObservation:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

