import os
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .base import CardReaderProtocolError, DESFireStatusError

STATUS_ADDITIONAL_FRAME = bytes.fromhex("91AF")
STATUS_OK = bytes.fromhex("9100")


@dataclass(frozen=True, slots=True)
class DESFireISOSession:
    key_number: int
    session_key: bytes


@dataclass(frozen=True, slots=True)
class DESFireAESSession:
    key_number: int
    session_key: bytes


def wrap_native_apdu(instruction: int, data: bytes = b"") -> bytes:
    if not 0 <= instruction <= 0xFF:
        raise ValueError("instruction debe caber en un byte")
    if len(data) > 0xFF:
        raise ValueError("La APDU corta admite como máximo 255 bytes de datos")
    if data:
        return bytes((0x90, instruction, 0x00, 0x00, len(data))) + data + b"\x00"
    return bytes((0x90, instruction, 0x00, 0x00, 0x00))


def split_native_response(response: bytes) -> tuple[bytes, bytes]:
    if len(response) < 2:
        raise CardReaderProtocolError("La respuesta DESFire no contiene un estado válido.")
    return response[:-2], response[-2:]


def require_status(response: bytes, expected: bytes) -> bytes:
    data, status = split_native_response(response)
    if status != expected:
        raise DESFireStatusError(status.hex().upper())
    return data


def rotate_left(value: bytes) -> bytes:
    if not value:
        raise ValueError("No se puede rotar una cadena vacía")
    return value[1:] + value[:1]


def _tdes_cbc(key: bytes, iv: bytes, data: bytes, *, encrypt: bool) -> bytes:
    if len(key) not in {16, 24}:
        raise ValueError("La clave TDES debe contener 16 o 24 bytes")
    if len(iv) != 8 or len(data) % 8:
        raise ValueError("TDES-CBC requiere IV de 8 bytes y bloques completos")
    expanded_key = key + key[:8] if len(key) == 16 else key
    cipher = Cipher(TripleDES(expanded_key), modes.CBC(iv))
    context = cipher.encryptor() if encrypt else cipher.decryptor()
    return context.update(data) + context.finalize()


def authenticate_iso_2k3des(
    exchange_apdu: Callable[[bytes], bytes],
    *,
    key_number: int = 0,
    key: bytes = bytes(16),
    random_bytes: Callable[[int], bytes] = os.urandom,
) -> DESFireISOSession:
    if not 0 <= key_number <= 0x0D:
        raise ValueError("key_number debe estar entre 0 y 13")
    encrypted_rnd_b = require_status(
        exchange_apdu(wrap_native_apdu(0x1A, bytes((key_number,)))),
        STATUS_ADDITIONAL_FRAME,
    )
    challenge_size = len(encrypted_rnd_b)
    if challenge_size not in {8, 16}:
        raise CardReaderProtocolError(
            "AuthenticateISO no devolvió un reto DES/TDES de 8 o 16 bytes."
        )

    rnd_b = _tdes_cbc(key, bytes(8), encrypted_rnd_b, encrypt=False)
    rnd_a = random_bytes(challenge_size)
    if len(rnd_a) != challenge_size:
        raise ValueError(f"random_bytes debe devolver exactamente {challenge_size} bytes")

    challenge = rnd_a + rotate_left(rnd_b)
    encrypted_challenge = _tdes_cbc(
        key,
        encrypted_rnd_b[-8:],
        challenge,
        encrypt=True,
    )
    encrypted_rotated_rnd_a = require_status(
        exchange_apdu(wrap_native_apdu(0xAF, encrypted_challenge)),
        STATUS_OK,
    )
    if len(encrypted_rotated_rnd_a) != challenge_size:
        raise CardReaderProtocolError(
            "AuthenticateISO devolvió una confirmación DES/TDES de longitud inválida."
        )
    rotated_rnd_a = _tdes_cbc(
        key,
        encrypted_challenge[-8:],
        encrypted_rotated_rnd_a,
        encrypt=False,
    )
    if rotated_rnd_a != rotate_left(rnd_a):
        raise CardReaderProtocolError("La tarjeta no superó la verificación mutua AuthenticateISO.")

    if challenge_size == 8:
        session_key = rnd_a[:4] + rnd_b[:4]
    else:
        session_key = rnd_a[:4] + rnd_b[:4] + rnd_a[12:] + rnd_b[12:]
    return DESFireISOSession(key_number=key_number, session_key=session_key)


def _aes_encrypt_block(key: bytes, block: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def _aes_cbc(key: bytes, iv: bytes, data: bytes, *, encrypt: bool) -> bytes:
    if len(key) != 16:
        raise ValueError("La clave AES debe contener exactamente 16 bytes")
    if len(iv) != 16 or len(data) % 16:
        raise ValueError("AES-CBC requiere IV de 16 bytes y bloques completos")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    context = cipher.encryptor() if encrypt else cipher.decryptor()
    return context.update(data) + context.finalize()


def _shift_left_128(value: bytes) -> bytes:
    shifted = int.from_bytes(value, "big") << 1
    return (shifted & ((1 << 128) - 1)).to_bytes(16, "big")


def _cmac_subkeys(key: bytes) -> tuple[bytes, bytes]:
    l_value = _aes_encrypt_block(key, bytes(16))
    k1 = _shift_left_128(l_value)
    if l_value[0] & 0x80:
        k1 = k1[:-1] + bytes((k1[-1] ^ 0x87,))
    k2 = _shift_left_128(k1)
    if k1[0] & 0x80:
        k2 = k2[:-1] + bytes((k2[-1] ^ 0x87,))
    return k1, k2


def diversify_aes128(master_key: bytes, diversification_input: bytes) -> bytes:
    if len(master_key) != 16:
        raise ValueError("La clave maestra AES debe contener exactamente 16 bytes")
    if not 1 <= len(diversification_input) <= 31:
        raise ValueError("La entrada de diversificación debe contener entre 1 y 31 bytes")

    prepared = b"\x01" + diversification_input
    padded = len(prepared) < 32
    if padded:
        prepared += b"\x80" + bytes(31 - len(prepared))
    k1, k2 = _cmac_subkeys(master_key)
    final_subkey = k2 if padded else k1
    first_block = prepared[:16]
    final_block = bytes(a ^ b for a, b in zip(prepared[16:], final_subkey, strict=True))
    encryptor = Cipher(algorithms.AES(master_key), modes.CBC(bytes(16))).encryptor()
    encrypted = encryptor.update(first_block + final_block) + encryptor.finalize()
    return encrypted[-16:]


def authenticate_aes(
    exchange_apdu: Callable[[bytes], bytes],
    *,
    key_number: int,
    key: bytes,
    random_bytes: Callable[[int], bytes] = os.urandom,
) -> DESFireAESSession:
    if not 0 <= key_number <= 0x0D:
        raise ValueError("key_number debe estar entre 0 y 13")
    if len(key) != 16:
        raise ValueError("La clave AES debe contener exactamente 16 bytes")

    encrypted_rnd_b = require_status(
        exchange_apdu(wrap_native_apdu(0xAA, bytes((key_number,)))),
        STATUS_ADDITIONAL_FRAME,
    )
    if len(encrypted_rnd_b) != 16:
        raise CardReaderProtocolError("AuthenticateAES no devolvió un reto de 16 bytes.")

    rnd_b = _aes_cbc(key, bytes(16), encrypted_rnd_b, encrypt=False)
    rnd_a = random_bytes(16)
    if len(rnd_a) != 16:
        raise ValueError("random_bytes debe devolver exactamente 16 bytes")
    encrypted_challenge = _aes_cbc(
        key,
        encrypted_rnd_b,
        rnd_a + rotate_left(rnd_b),
        encrypt=True,
    )
    encrypted_rotated_rnd_a = require_status(
        exchange_apdu(wrap_native_apdu(0xAF, encrypted_challenge)),
        STATUS_OK,
    )
    if len(encrypted_rotated_rnd_a) != 16:
        raise CardReaderProtocolError(
            "AuthenticateAES devolvió una confirmación de longitud inválida."
        )
    rotated_rnd_a = _aes_cbc(
        key,
        encrypted_challenge[-16:],
        encrypted_rotated_rnd_a,
        encrypt=False,
    )
    if rotated_rnd_a != rotate_left(rnd_a):
        raise CardReaderProtocolError("La tarjeta no superó la verificación mutua AuthenticateAES.")

    session_key = rnd_a[:4] + rnd_b[:4] + rnd_a[12:] + rnd_b[12:]
    return DESFireAESSession(key_number=key_number, session_key=session_key)


def desfire_crc32(data: bytes) -> bytes:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _bit in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc.to_bytes(4, "little")


def aes_cmac_with_iv(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("La clave y el IV de CMAC AES deben contener 16 bytes")
    k1, k2 = _cmac_subkeys(key)
    complete = bool(data) and len(data) % 16 == 0
    if complete:
        prefix = data[:-16]
        final = bytes(a ^ b for a, b in zip(data[-16:], k1, strict=True))
    else:
        split = len(data) - (len(data) % 16)
        prefix = data[:split]
        tail = data[split:] + b"\x80"
        tail += bytes(16 - len(tail))
        final = bytes(a ^ b for a, b in zip(tail, k2, strict=True))
    encrypted = _aes_cbc(key, iv, prefix + final, encrypt=True)
    return encrypted[-16:]


def change_aes_key(
    exchange_apdu: Callable[[bytes], bytes],
    *,
    authenticated_key_number: int,
    target_key_number: int,
    session_key: bytes,
    old_key: bytes,
    new_key: bytes,
    new_key_version: int = 1,
) -> None:
    if len(session_key) != 16 or len(old_key) != 16 or len(new_key) != 16:
        raise ValueError("Las claves de ChangeKey AES deben contener 16 bytes")
    if not 0 <= new_key_version <= 0xFF:
        raise ValueError("new_key_version debe caber en un byte")
    if not 0 <= authenticated_key_number <= 0x0D:
        raise ValueError("authenticated_key_number debe estar entre 0 y 13")
    if not 0 <= target_key_number <= 0x0D:
        raise ValueError("target_key_number debe estar entre 0 y 13")

    different_key = authenticated_key_number != target_key_number
    encoded_key = (
        bytes(a ^ b for a, b in zip(new_key, old_key, strict=True)) if different_key else new_key
    )
    clear_command = bytes((0xC4, target_key_number)) + encoded_key + bytes((new_key_version,))
    protected = encoded_key + bytes((new_key_version,)) + desfire_crc32(clear_command)
    if different_key:
        protected += desfire_crc32(new_key)
    protected += bytes((-len(protected)) % 16)
    encrypted = _aes_cbc(session_key, bytes(16), protected, encrypt=True)
    require_status(
        exchange_apdu(wrap_native_apdu(0xC4, bytes((target_key_number,)) + encrypted)),
        STATUS_OK,
    )


def derive_card_key(
    master_key: bytes,
    *,
    uid: bytes,
    application_id: bytes,
    key_number: int,
) -> bytes:
    if not uid:
        raise ValueError("El UID no puede estar vacío")
    if len(application_id) != 3:
        raise ValueError("El AID DESFire debe contener exactamente 3 bytes")
    if not 0 <= key_number <= 0x0D:
        raise ValueError("key_number debe estar entre 0 y 13")
    diversification_input = b"DEMP" + uid + application_id + bytes((key_number,))
    return diversify_aes128(master_key, diversification_input)
