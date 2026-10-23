from .base import (
    DESFireStatusError,
    DESFireVersionComponent,
    UnsupportedCard,
)

STATUS_ADDITIONAL_FRAME = bytes.fromhex("91AF")
STATUS_OK = bytes.fromhex("9100")

APDU_GET_VERSION = bytes.fromhex("9060000000")
APDU_ADDITIONAL_FRAME = bytes.fromhex("90AF000000")
APDU_GET_APPLICATION_IDS = bytes.fromhex("906A000000")
APDU_GET_FREE_MEMORY = bytes.fromhex("906E000000")


def decode_storage_size(storage_size_code: int) -> int:
    exponent = storage_size_code >> 1
    return 1 << exponent


def decode_bcd(value: int, *, field: str) -> int:
    high, low = value >> 4, value & 0x0F
    if high > 9 or low > 9:
        raise UnsupportedCard(f"El campo BCD {field} de la tarjeta es inválido.")
    return high * 10 + low


def parse_version_component(data: bytes) -> DESFireVersionComponent:
    if len(data) != 7:
        raise UnsupportedCard("El bloque GetVersion de DESFire no tiene 7 bytes.")
    return DESFireVersionComponent(
        vendor_id=data[0],
        type_id=data[1],
        subtype=data[2],
        major=data[3],
        minor=data[4],
        storage_size_code=data[5],
        protocol=data[6],
    )


def parse_desfire_status(response: bytes) -> tuple[bytes, bytes]:
    if len(response) < 2:
        raise UnsupportedCard("La respuesta APDU de la tarjeta es demasiado corta.")
    return response[:-2], response[-2:]


def require_desfire_ok(response: bytes) -> bytes:
    data, status = parse_desfire_status(response)
    if status != STATUS_OK:
        raise DESFireStatusError(status.hex().upper())
    return data


def card_type_from_software(component: DESFireVersionComponent) -> str:
    if component.vendor_id != 0x04 or component.type_id != 0x01:
        return "iso14443_4_type_a"
    generations = {1: "ev1", 2: "ev2", 3: "ev3"}
    generation = generations.get(component.major, f"version_{component.major}")
    return f"mifare_desfire_{generation}"
