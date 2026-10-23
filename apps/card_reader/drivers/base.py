from abc import ABC, abstractmethod
from dataclasses import dataclass


class CardReaderError(Exception):
    code = "card_reader_error"


class CardReaderNotConnected(CardReaderError):
    code = "card_reader_not_connected"


class CardReaderNoCard(CardReaderError):
    code = "card_reader_no_card"


class CardReaderConnectionError(CardReaderError):
    code = "card_reader_connection_error"


class CardReaderProtocolError(CardReaderError):
    code = "card_reader_protocol_error"


class CardReaderConfigurationError(CardReaderError):
    code = "card_reader_configuration_error"


class UnsupportedCard(CardReaderError):
    code = "unsupported_card"


class DESFireStatusError(CardReaderError):
    code = "desfire_status_error"

    def __init__(self, status_hex: str) -> None:
        self.status_hex = status_hex
        super().__init__(f"La tarjeta DESFire respondió con estado {status_hex}.")


@dataclass(frozen=True, slots=True)
class CardReaderSnapshot:
    driver: str
    model: str
    connected: bool
    card_present: bool
    firmware_version: str
    port: str | None
    baud_rate: int | None


@dataclass(frozen=True, slots=True)
class DESFireVersionComponent:
    vendor_id: int
    type_id: int
    subtype: int
    major: int
    minor: int
    storage_size_code: int
    protocol: int


@dataclass(frozen=True, slots=True)
class CardInspection:
    card_type: str
    uid: str
    rf_uid: str
    atr: str
    hardware: DESFireVersionComponent
    software: DESFireVersionComponent
    batch_number: str
    production_week: int
    production_year: int
    capacity_bytes: int
    free_memory_bytes: int
    application_ids: tuple[str, ...]
    credential_application_id: str
    credential_state: str


@dataclass(frozen=True, slots=True)
class CardSecurityProbe:
    uid: str
    picc_key_settings: int
    picc_key_count: int
    picc_key_type: str
    factory_master_key_authenticated: bool


@dataclass(frozen=True, slots=True)
class CardProvisioningPlan:
    uid: str
    application_id: str
    credential_id: str
    card_type: str
    current_state: str
    will_create_aes_application: bool
    will_create_credential_file: bool
    will_personalize_keys: bool
    write_enabled: bool


@dataclass(frozen=True, slots=True)
class CardProvisioningResult:
    uid: str
    application_id: str
    credential_id: str
    application_created: bool
    credential_file_created: bool
    keys_personalized: bool
    verified: bool


@dataclass(frozen=True, slots=True)
class CardCredentialVerification:
    uid: str
    application_id: str
    credential_id: str
    authenticated: bool
    credential_version: int


class CardReaderDriver(ABC):
    @abstractmethod
    def connect(self) -> CardReaderSnapshot:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> CardReaderSnapshot:
        raise NotImplementedError

    @abstractmethod
    def inspect_card(self) -> CardInspection:
        raise NotImplementedError

    @abstractmethod
    def probe_security(self) -> CardSecurityProbe:
        raise NotImplementedError

    @abstractmethod
    def plan_provisioning(
        self,
        *,
        expected_uid: str,
        credential_id: str,
        write_enabled: bool,
    ) -> CardProvisioningPlan:
        raise NotImplementedError

    @abstractmethod
    def provision_card(
        self,
        *,
        expected_uid: str,
        credential_id: str,
        master_key: bytes,
    ) -> CardProvisioningResult:
        raise NotImplementedError

    @abstractmethod
    def verify_credential(self, *, master_key: bytes) -> CardCredentialVerification:
        raise NotImplementedError
