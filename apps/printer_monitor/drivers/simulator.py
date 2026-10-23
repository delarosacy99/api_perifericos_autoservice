import threading

from .base import PrinterMonitorDriver, PrinterObservation
from .status import MAPPING_SOURCE, decode_full_status


class SimulatedPrinterMonitorDriver(PrinterMonitorDriver):
    name = "simulator"

    def __init__(
        self,
        *,
        raw_status: int = 0,
        paper_remaining_cm: int | None = None,
        mapping_physically_validated: bool = False,
    ) -> None:
        self._raw_status = raw_status
        self._paper_remaining_cm = paper_remaining_cm
        self._mapping_physically_validated = mapping_physically_validated
        self._lock = threading.RLock()

    def read_status(self) -> PrinterObservation:
        with self._lock:
            decoded = decode_full_status(self._raw_status)
            remaining = self._paper_remaining_cm if decoded.paper_near_end else None
            return PrinterObservation(
                device_id="printer",
                driver=self.name,
                model="CUSTOM-K80-SIMULATOR",
                printer_name="CUSTOM K80",
                connected=True,
                monitoring_only=True,
                state=decoded.state,
                ready=decoded.ready,
                alerts=decoded.alerts,
                paper_remaining_cm=remaining,
                paper_remaining_estimated=remaining is not None,
                paper_remaining_error=None,
                raw_status=self._raw_status,
                raw_status_hex=f"{self._raw_status:08X}",
                status_bytes=decoded.status_bytes,
                interface_number=None,
                model_gsi_hex=None,
                mapping_source=MAPPING_SOURCE,
                mapping_physically_validated=self._mapping_physically_validated,
                **{
                    field: getattr(decoded, field)
                    for field in (
                        "paper_out",
                        "paper_near_end",
                        "paper_end",
                        "virtual_paper_end",
                        "ticket_out",
                        "print_head_up",
                        "print_head_up_or_paper_jam",
                        "cover_open",
                        "paper_jam",
                        "cutter_error",
                        "over_temperature",
                        "command_error",
                    )
                },
            )

    def disconnect(self) -> None:
        return None

    def set_status(self, raw_status: int, *, paper_remaining_cm: int | None = None) -> None:
        with self._lock:
            self._raw_status = raw_status
            self._paper_remaining_cm = paper_remaining_cm

