from abc import ABC, abstractmethod
from dataclasses import dataclass


class DoorMonitorError(Exception):
    code = "door_monitor_error"


class DoorMonitorConnectionError(DoorMonitorError):
    code = "door_monitor_connection_error"


class DoorMonitorProtocolError(DoorMonitorError):
    code = "door_monitor_protocol_error"


class DoorMonitorConfigurationError(DoorMonitorError):
    code = "door_monitor_configuration_error"


@dataclass(frozen=True, slots=True)
class DoorObservation:
    door_id: str
    door_name: str
    coverage: str
    driver: str
    model: str
    connected: bool
    monitoring_only: bool
    state: str
    is_open: bool | None
    raw_status_hex: str
    sdk_documented_status: bool
    physically_calibrated_status: bool
    port: str | None
    baud_rate: int | None


class DoorMonitorDriver(ABC):
    @abstractmethod
    def read_status(self) -> DoorObservation:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError
