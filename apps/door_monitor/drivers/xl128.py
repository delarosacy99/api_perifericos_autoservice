import threading
from collections.abc import Callable
from dataclasses import dataclass

import serial

from .base import (
    DoorMonitorConfigurationError,
    DoorMonitorConnectionError,
    DoorMonitorDriver,
    DoorObservation,
)

QUERY_COMMAND = bytes.fromhex("1B70")
SDK_DOCUMENTED_STATUS_CODES = frozenset({0x10, 0x08, 0x04, 0x02, 0x01})


@dataclass(frozen=True, slots=True)
class XL128SerialConfig:
    port: str
    baud_rate: int = 9600
    timeout_seconds: float = 1.0
    write_timeout_seconds: float = 0.5
    attempts: int = 2


class XL128SerialTransport:
    def __init__(
        self,
        config: XL128SerialConfig,
        *,
        serial_factory: Callable = serial.Serial,
    ) -> None:
        self.config = config
        self._serial_factory = serial_factory
        self._serial = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    def open(self) -> None:
        with self._lock:
            if self.is_open:
                return
            try:
                self._serial = self._serial_factory(
                    port=self.config.port,
                    baudrate=self.config.baud_rate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.config.timeout_seconds,
                    write_timeout=self.config.write_timeout_seconds,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )
            except (OSError, serial.SerialException) as exc:
                self._serial = None
                raise DoorMonitorConnectionError(
                    f"No se pudo abrir {self.config.port} para el XL128: {exc}"
                ) from exc

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                finally:
                    self._serial = None

    def query(self) -> int:
        last_error: DoorMonitorConnectionError | None = None
        with self._lock:
            self.open()
            for _attempt in range(self.config.attempts):
                try:
                    self._serial.reset_input_buffer()
                    written = self._serial.write(QUERY_COMMAND)
                    if written != len(QUERY_COMMAND):
                        raise DoorMonitorConnectionError(
                            "No se pudo enviar la consulta completa al XL128."
                        )
                    self._serial.flush()
                    response = self._serial.read(1)
                    if len(response) != 1:
                        raise DoorMonitorConnectionError(
                            "El XL128 no respondió antes de vencer el tiempo de espera."
                        )
                    return response[0]
                except (OSError, serial.SerialException) as exc:
                    last_error = DoorMonitorConnectionError(
                        f"Falló la comunicación serial con el XL128: {exc}"
                    )
                except DoorMonitorConnectionError as exc:
                    last_error = exc
            self.close()
        if last_error is not None:
            raise last_error
        raise DoorMonitorConnectionError("Falló la consulta de estado al XL128.")


class XL128SerialDoorMonitor(DoorMonitorDriver):
    name = "xl128_serial"

    def __init__(
        self,
        config: XL128SerialConfig,
        *,
        open_codes: tuple[int, ...] = (),
        closed_codes: tuple[int, ...] = (),
        transport: XL128SerialTransport | None = None,
    ) -> None:
        self.config = config
        self.open_codes = frozenset(open_codes)
        self.closed_codes = frozenset(closed_codes)
        if self.open_codes & self.closed_codes:
            raise DoorMonitorConfigurationError(
                "Un código XL128 no puede representar puerta abierta y cerrada al mismo tiempo."
            )
        self.transport = transport or XL128SerialTransport(config)

    def read_status(self) -> DoorObservation:
        raw_status = self.transport.query()
        if raw_status in self.open_codes:
            state = "open"
            is_open = True
        elif raw_status in self.closed_codes:
            state = "closed"
            is_open = False
        else:
            state = "unknown"
            is_open = None
        return DoorObservation(
            door_id="front",
            door_name="Puerta frontal",
            coverage="front_door_only",
            driver=self.name,
            model="XL128 V1.2",
            connected=True,
            monitoring_only=True,
            state=state,
            is_open=is_open,
            raw_status_hex=f"{raw_status:02X}",
            sdk_documented_status=raw_status in SDK_DOCUMENTED_STATUS_CODES,
            physically_calibrated_status=(
                raw_status in self.open_codes or raw_status in self.closed_codes
            ),
            port=self.config.port,
            baud_rate=self.config.baud_rate,
        )

    def disconnect(self) -> None:
        self.transport.close()
