import secrets
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import serial
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SSP_STX = 0x7F
SSP_STEX = 0x7E
SSP_CRC_SEED = 0xFFFF
SSP_CRC_POLYNOMIAL = 0x8005
SSP_MAX_ADDRESS = 0x7D

SSP_RESPONSE_OK = 0xF0
SSP_RESPONSE_NAMES = {
    0xF0: "OK",
    0xF2: "COMMAND_NOT_KNOWN",
    0xF3: "WRONG_NUMBER_OF_PARAMETERS",
    0xF4: "PARAMETER_OUT_OF_RANGE",
    0xF5: "COMMAND_CANNOT_BE_PROCESSED",
    0xF6: "SOFTWARE_ERROR",
    0xF8: "FAIL",
    0xFA: "KEY_NOT_SET",
}


class SSPError(Exception):
    """Base error for the eSSP implementation."""


class SSPTransportError(SSPError):
    pass


class SSPTimeoutError(SSPTransportError):
    pass


class SSPPacketError(SSPError):
    pass


class SSPResponseError(SSPError):
    def __init__(self, status: int, response: bytes) -> None:
        self.status = status
        self.response = response
        name = SSP_RESPONSE_NAMES.get(status, "UNKNOWN_RESPONSE")
        super().__init__(f"El dispositivo respondió {name} (0x{status:02X}).")


def crc16_ssp(data: bytes) -> int:
    """Forward CRC-16 used by SSP (seed 0xFFFF, polynomial 0x8005)."""

    crc = SSP_CRC_SEED
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ SSP_CRC_POLYNOMIAL) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_frame(*, address: int, sequence: int, data: bytes) -> bytes:
    if not 0 <= address <= SSP_MAX_ADDRESS:
        raise ValueError("La dirección SSP debe estar entre 0x00 y 0x7D.")
    if sequence not in {0x00, 0x80}:
        raise ValueError("La secuencia SSP debe ser 0x00 o 0x80.")
    if not 1 <= len(data) <= 0xFF:
        raise ValueError("La trama SSP debe contener entre 1 y 255 bytes de datos.")

    body = bytes((address | sequence, len(data))) + data
    crc = crc16_ssp(body)
    raw_frame = bytes((SSP_STX,)) + body + struct.pack("<H", crc)
    return raw_frame[:1] + raw_frame[1:].replace(bytes((SSP_STX,)), bytes((SSP_STX, SSP_STX)))


def decode_frame(frame: bytes) -> tuple[int, int, bytes]:
    """Decode an already de-stuffed SSP frame."""

    if len(frame) < 6 or frame[0] != SSP_STX:
        raise SSPPacketError("La trama SSP no tiene un encabezado válido.")
    expected_length = frame[2] + 5
    if len(frame) != expected_length:
        raise SSPPacketError(
            f"Longitud SSP inválida: se esperaban {expected_length} bytes y llegaron {len(frame)}."
        )

    expected_crc = struct.unpack("<H", frame[-2:])[0]
    actual_crc = crc16_ssp(frame[1:-2])
    if actual_crc != expected_crc:
        raise SSPPacketError(
            f"CRC SSP inválido: recibido 0x{expected_crc:04X}, calculado 0x{actual_crc:04X}."
        )

    address_sequence = frame[1]
    return address_sequence & 0x7F, address_sequence & 0x80, frame[3:-2]


class SSPStreamDecoder:
    """Incrementally removes SSP byte stuffing and yields complete raw frames."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._buffer = bytearray()
        self._pending_stx = False

    def feed(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for value in chunk:
            if not self._buffer:
                if value == SSP_STX:
                    self._buffer.append(value)
                continue

            if self._pending_stx:
                if value == SSP_STX:
                    self._buffer.append(SSP_STX)
                else:
                    # A non-stuffed STX starts a new packet. The current byte is its address.
                    self._buffer = bytearray((SSP_STX, value))
                self._pending_stx = False
            elif value == SSP_STX:
                self._pending_stx = True
            else:
                self._buffer.append(value)

            if len(self._buffer) >= 3:
                expected_length = self._buffer[2] + 5
                if len(self._buffer) == expected_length:
                    frames.append(bytes(self._buffer))
                    self.reset()
                elif len(self._buffer) > expected_length:
                    self.reset()
        return frames


@dataclass(frozen=True, slots=True)
class SSPSerialConfig:
    port: str
    baud_rate: int = 9600
    timeout_seconds: float = 1.0
    write_timeout_seconds: float = 0.5
    attempts: int = 3


class SerialSSPTransport:
    """Exclusive, synchronized pyserial transport for one SSP bus."""

    def __init__(
        self,
        config: SSPSerialConfig,
        *,
        serial_factory: Callable[..., object] = serial.Serial,
    ) -> None:
        self.config = config
        self._serial_factory = serial_factory
        self._serial = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return bool(self._serial is not None and getattr(self._serial, "is_open", False))

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
                    stopbits=serial.STOPBITS_TWO,
                    timeout=min(self.config.timeout_seconds, 0.05),
                    write_timeout=self.config.write_timeout_seconds,
                )
                reset_input = getattr(self._serial, "reset_input_buffer", None)
                if reset_input is not None:
                    reset_input()
            except (OSError, serial.SerialException) as exc:
                self._serial = None
                raise SSPTransportError(f"No se pudo abrir {self.config.port}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._serial is None:
                return
            try:
                self._serial.close()
            except (OSError, serial.SerialException) as exc:
                raise SSPTransportError(f"No se pudo cerrar el puerto serie: {exc}") from exc
            finally:
                self._serial = None

    def exchange(self, packet: bytes, *, expected_address_sequence: int) -> bytes:
        with self._lock:
            if not self.is_open:
                raise SSPTransportError("El puerto serie SSP no está abierto.")

            last_packet_error: SSPPacketError | None = None
            for _attempt in range(self.config.attempts):
                decoder = SSPStreamDecoder()
                try:
                    written = self._serial.write(packet)
                    if written is not None and written != len(packet):
                        raise SSPTransportError(
                            f"Escritura serie incompleta: {written} de {len(packet)} bytes."
                        )
                    flush = getattr(self._serial, "flush", None)
                    if flush is not None:
                        flush()
                except (OSError, serial.SerialException) as exc:
                    raise SSPTransportError(f"Falló la escritura SSP: {exc}") from exc

                deadline = time.monotonic() + self.config.timeout_seconds
                while time.monotonic() < deadline:
                    try:
                        chunk = self._serial.read(1)
                    except (OSError, serial.SerialException) as exc:
                        raise SSPTransportError(f"Falló la lectura SSP: {exc}") from exc
                    if not chunk:
                        continue

                    for frame in decoder.feed(chunk):
                        try:
                            address, sequence, _data = decode_frame(frame)
                        except SSPPacketError as exc:
                            last_packet_error = exc
                            continue
                        if address | sequence == expected_address_sequence:
                            return frame

            if last_packet_error is not None:
                raise last_packet_error
            raise SSPTimeoutError(
                f"El dispositivo SSP no respondió después de {self.config.attempts} intentos."
            )


class SSPCipher:
    def __init__(self, *, fixed_key: int, variable_key: int) -> None:
        if not 0 <= fixed_key <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("La clave fija eSSP debe ocupar 64 bits.")
        if not 0 <= variable_key <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("La clave variable eSSP debe ocupar 64 bits.")
        self._key = struct.pack("<Q", fixed_key) + struct.pack("<Q", variable_key)

    def encrypt(self, data: bytes, *, counter: int, padding: bytes | None = None) -> bytes:
        packet_length = len(data) + 7
        padding_length = (-packet_length) % 16
        if padding is None:
            padding = secrets.token_bytes(padding_length)
        elif len(padding) != padding_length:
            raise ValueError(f"El relleno cifrado debe tener {padding_length} bytes.")

        plaintext = bytes((len(data),)) + struct.pack("<I", counter) + data + padding
        plaintext += struct.pack("<H", crc16_ssp(plaintext))
        encryptor = Cipher(algorithms.AES(self._key), modes.ECB()).encryptor()
        return bytes((SSP_STEX,)) + encryptor.update(plaintext) + encryptor.finalize()

    def decrypt(self, data: bytes, *, expected_counter: int) -> bytes:
        if not data or data[0] != SSP_STEX:
            raise SSPPacketError("La respuesta no contiene un bloque cifrado eSSP.")
        ciphertext = data[1:]
        if not ciphertext or len(ciphertext) % 16:
            raise SSPPacketError("La longitud del bloque AES eSSP no es válida.")

        decryptor = Cipher(algorithms.AES(self._key), modes.ECB()).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        expected_crc = struct.unpack("<H", plaintext[-2:])[0]
        actual_crc = crc16_ssp(plaintext[:-2])
        if actual_crc != expected_crc:
            raise SSPPacketError("El CRC interno del bloque cifrado eSSP no coincide.")

        data_length = plaintext[0]
        if data_length > len(plaintext) - 7:
            raise SSPPacketError("La longitud interna del bloque cifrado eSSP no es válida.")
        counter = struct.unpack("<I", plaintext[1:5])[0]
        if counter != expected_counter:
            raise SSPPacketError(
                f"Contador eSSP inválido: recibido {counter}, esperado {expected_counter}."
            )
        return plaintext[5 : 5 + data_length]


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    for small_prime in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if value == small_prime:
            return True
        if value % small_prime == 0:
            return False

    exponent = value - 1
    shifts = 0
    while exponent % 2 == 0:
        shifts += 1
        exponent //= 2
    for base in (2, 3, 5, 7, 11):
        if base >= value:
            continue
        result = pow(base, exponent, value)
        if result in {1, value - 1}:
            continue
        for _ in range(shifts - 1):
            result = pow(result, 2, value)
            if result == value - 1:
                break
        else:
            return False
    return True


def generate_prime() -> int:
    candidate = secrets.randbelow((1 << 31) - 3) + 3
    candidate |= 1
    while not _is_prime(candidate):
        candidate += 2
        if candidate >= 1 << 31:
            candidate = 3
    return candidate


class SSPClient:
    DEFAULT_FIXED_KEY = 0x0123456701234567

    def __init__(
        self,
        transport: SerialSSPTransport,
        *,
        address: int = 0,
        fixed_key: int = DEFAULT_FIXED_KEY,
    ) -> None:
        if not 0 <= address <= SSP_MAX_ADDRESS:
            raise ValueError("La dirección SSP debe estar entre 0x00 y 0x7D.")
        self.transport = transport
        self.address = address
        self.fixed_key = fixed_key
        self.sequence = 0x00
        self.encrypted_packet_count = 0
        self.encryption_enabled = False
        self._cipher: SSPCipher | None = None
        self._lock = threading.RLock()

    def open(self) -> None:
        self.transport.open()

    def close(self) -> None:
        self.transport.close()

    def send(self, data: bytes, *, encrypted: bool | None = None) -> bytes:
        if not data:
            raise ValueError("Un comando SSP no puede estar vacío.")
        with self._lock:
            use_encryption = self.encryption_enabled if encrypted is None else encrypted
            if data[0] == 0x11:
                self.sequence = 0x80

            wire_data = data
            response_counter = self.encrypted_packet_count
            if use_encryption:
                if self._cipher is None:
                    raise SSPPacketError("No hay una clave eSSP negociada.")
                wire_data = self._cipher.encrypt(
                    data,
                    counter=self.encrypted_packet_count,
                )
                self.encrypted_packet_count += 1
                response_counter = self.encrypted_packet_count

            address_sequence = self.address | self.sequence
            packet = encode_frame(address=self.address, sequence=self.sequence, data=wire_data)
            raw_response = self.transport.exchange(
                packet,
                expected_address_sequence=address_sequence,
            )
            _address, _sequence, response_data = decode_frame(raw_response)

            if response_data and response_data[0] == SSP_STEX:
                if self._cipher is None:
                    raise SSPPacketError(
                        "Se recibió una respuesta cifrada antes de negociar la clave."
                    )
                response_data = self._cipher.decrypt(
                    response_data,
                    expected_counter=response_counter,
                )
            elif use_encryption:
                raise SSPPacketError("El dispositivo respondió sin cifrado a un comando cifrado.")

            self.sequence ^= 0x80
            return response_data

    def command(
        self,
        command: int,
        parameters: bytes = b"",
        *,
        require_ok: bool = True,
        encrypted: bool | None = None,
    ) -> bytes:
        response = self.send(bytes((command,)) + parameters, encrypted=encrypted)
        if not response:
            raise SSPPacketError("El dispositivo devolvió una respuesta SSP vacía.")
        if require_ok and response[0] != SSP_RESPONSE_OK:
            raise SSPResponseError(response[0], response)
        return response

    def negotiate_keys(self) -> int:
        """Perform the ITL Diffie-Hellman exchange and enable AES-128 eSSP."""

        with self._lock:
            self.encryption_enabled = False
            self._cipher = None
            self.encrypted_packet_count = 0
            self.command(0x11, encrypted=False)

            generator = generate_prime()
            modulus = generate_prime()
            if generator < modulus:
                generator, modulus = modulus, generator
            host_random = secrets.randbelow(1 << 31)
            host_intermediate = pow(generator, host_random, modulus)

            self.command(0x4A, struct.pack("<Q", generator), encrypted=False)
            self.command(0x4B, struct.pack("<Q", modulus), encrypted=False)
            response = self.command(
                0x4C,
                struct.pack("<Q", host_intermediate),
                encrypted=False,
            )
            if len(response) < 9:
                raise SSPPacketError("La respuesta de intercambio de claves eSSP está incompleta.")

            slave_intermediate = struct.unpack("<Q", response[1:9])[0]
            variable_key = pow(slave_intermediate, host_random, modulus)
            self._cipher = SSPCipher(
                fixed_key=self.fixed_key,
                variable_key=variable_key,
            )
            self.encrypted_packet_count = 0
            self.encryption_enabled = True
            return variable_key
