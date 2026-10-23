from dataclasses import asdict

from django.conf import settings

from .drivers.base import (
    CardCredentialVerification,
    CardInspection,
    CardReaderConfigurationError,
    CardReaderSnapshot,
)


def serialize_snapshot(snapshot: CardReaderSnapshot) -> dict:
    return asdict(snapshot)


def serialize_inspection(inspection: CardInspection) -> dict:
    return asdict(inspection)


def serialize_credential_verification(
    verification: CardCredentialVerification,
) -> dict:
    return asdict(verification)


def get_card_master_key() -> bytes:
    value = settings.CARD_PROVISIONING_MASTER_KEY_HEX
    if not value:
        raise CardReaderConfigurationError("CARD_PROVISIONING_MASTER_KEY_HEX no está configurada.")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise CardReaderConfigurationError(
            "CARD_PROVISIONING_MASTER_KEY_HEX debe contener 32 caracteres hexadecimales."
        ) from exc
    if len(key) != 16:
        raise CardReaderConfigurationError(
            "CARD_PROVISIONING_MASTER_KEY_HEX debe contener exactamente 16 bytes."
        )
    if key == bytes(16):
        raise CardReaderConfigurationError(
            "CARD_PROVISIONING_MASTER_KEY_HEX no puede ser la clave AES cero."
        )
    return key
