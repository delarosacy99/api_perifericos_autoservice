import threading
from collections.abc import Callable
from dataclasses import dataclass

import serial

from .base import CardReaderConnectionError, CardReaderProtocolError


def xor_checksum(data: bytes) -> int:
    checksum = 0
    for value in data:
        checksum ^= value
    return checksum


def encode_frame(command: int, data: bytes = b"") -> bytes:
    if not 0 <= command <= 0xFF:
        raise ValueError("command debe caber en un byte")
    if len(data) > 0xFD:
        raise ValueError("La trama CRT-603 admite como máximo 253 bytes de datos")
    body = bytes((len(data) + 2, command)) + data
    return body + bytes((xor_checksum(body),))


def decode_frame(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 3:
        raise CardReaderProtocolError("La respuesta CRT-603 es demasiado corta.")
    declared_length = frame[0]
    if declared_length < 2:
        raise CardReaderProtocolError("La longitud declarada por el CRT-603 es inválida.")
    if len(frame) != declared_length + 1:
        raise CardReaderProtocolError(
            "La longitud de la respuesta CRT-603 no coincide con su encabezado."
        )
    if xor_checksum(frame[:-1]) != frame[-1]:
        raise CardReaderProtocolError("El BCC XOR de la respuesta CRT-603 es inválido.")
    return frame[1], frame[2:-1]


class CRT603CommandError(CardReaderProtocolError):
    code = "crt603_command_error"

    def __init__(
        self,
        *,
        command: int,
        reply_command: int,
        error_code: int | None,
    ) -> None:
        self.command = command
        self.reply_command = reply_command
        self.error_code = error_code
        message = (
            f"El CRT-603 rechazó el comando 0x{command:02X} con respuesta 0x{reply_command:02X}."
        )
        if error_code is not None:
            message += f" Código 0x{error_code:02X}."
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CRT603SerialConfig:
    port: str
    baud_rate: int = 9600
    timeout_seconds: float = 1.0
    write_timeout_seconds: float = 0.5
    attempts: int = 2


class CRT603SerialTransport:
    def __init__(
        self,
        config: CRT603SerialConfig,
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
                )
            except (OSError, serial.SerialException) as exc:
                self._serial = None
                raise CardReaderConnectionError(
                    f"No se pudo abrir {self.config.port} para el CRT-603: {exc}"
                ) from exc

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                finally:
                    self._serial = None

    def _read_exact(self, size: int) -> bytes:
        received = bytearray()
        while len(received) < size:
            chunk = self._serial.read(size - len(received))
            if not chunk:
                raise CardReaderConnectionError(
                    "El CRT-603 no respondió antes de vencer el tiempo de espera."
                )
            received.extend(chunk)
        return bytes(received)

    def _read_frame(self) -> bytes:
        first = self._read_exact(1)
        declared_length = first[0]
        if declared_length < 2:
            raise CardReaderProtocolError("El CRT-603 devolvió una longitud inválida.")
        return first + self._read_exact(declared_length)

    def exchange(self, command: int, data: bytes = b"") -> bytes:
        packet = encode_frame(command, data)
        last_error: Exception | None = None
        with self._lock:
            self.open()
            for _attempt in range(self.config.attempts):
                try:
                    self._serial.reset_input_buffer()
                    written = self._serial.write(packet)
                    if written != len(packet):
                        raise CardReaderConnectionError(
                            "No se pudo enviar la trama completa al CRT-603."
                        )
                    self._serial.flush()
                    reply_command, reply_data = decode_frame(self._read_frame())
                    if reply_command == command:
                        return reply_data
                    if reply_command == (command ^ 0xFF):
                        raise CRT603CommandError(
                            command=command,
                            reply_command=reply_command,
                            error_code=reply_data[0] if reply_data else None,
                        )
                    raise CardReaderProtocolError(
                        f"Se esperaba respuesta 0x{command:02X}, "
                        f"pero el CRT-603 devolvió 0x{reply_command:02X}."
                    )
                except CRT603CommandError:
                    raise
                except (OSError, serial.SerialException) as exc:
                    last_error = CardReaderConnectionError(
                        f"Falló la comunicación serial con el CRT-603: {exc}"
                    )
                except (CardReaderConnectionError, CardReaderProtocolError) as exc:
                    last_error = exc
            self.close()
        if last_error is not None:
            raise last_error
        raise CardReaderConnectionError("Falló la comunicación con el CRT-603.")
