import threading

from .base import DoorMonitorDriver, DoorObservation


class SimulatedDoorMonitorDriver(DoorMonitorDriver):
    name = "simulator"

    def __init__(self, *, is_open: bool = False) -> None:
        self._is_open = is_open
        self._connected = False
        self._lock = threading.RLock()

    def read_status(self) -> DoorObservation:
        with self._lock:
            self._connected = True
            return DoorObservation(
                door_id="front",
                door_name="Puerta frontal",
                coverage="front_door_only",
                driver=self.name,
                model="XL128-V1.2-SIMULATOR",
                connected=True,
                monitoring_only=True,
                state="open" if self._is_open else "closed",
                is_open=self._is_open,
                raw_status_hex="01" if self._is_open else "00",
                sdk_documented_status=self._is_open,
                physically_calibrated_status=True,
                port=None,
                baud_rate=None,
            )

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def set_open(self, is_open: bool) -> None:
        with self._lock:
            self._is_open = is_open
