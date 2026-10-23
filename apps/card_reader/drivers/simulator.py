import threading
import uuid

from .base import (
    CardCredentialVerification,
    CardInspection,
    CardProvisioningPlan,
    CardProvisioningResult,
    CardReaderDriver,
    CardReaderProtocolError,
    CardReaderSnapshot,
    CardSecurityProbe,
    DESFireVersionComponent,
)


class SimulatedCRT603Driver(CardReaderDriver):
    name = "simulator"

    def __init__(
        self,
        *,
        credential_application_id: str,
        application_ids: tuple[str, ...] = (),
    ) -> None:
        self.credential_application_id = credential_application_id.upper()
        self.application_ids = tuple(value.upper() for value in application_ids)
        self._lock = threading.RLock()
        self._connected = False
        self._card_present = False
        self._credential_id: str | None = None
        self._provisioning_master_key: bytes | None = None

    def connect(self) -> CardReaderSnapshot:
        with self._lock:
            self._connected = True
            return self.snapshot()

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._card_present = False

    def snapshot(self) -> CardReaderSnapshot:
        with self._lock:
            return CardReaderSnapshot(
                driver=self.name,
                model="CRT-603-SIMULATOR",
                connected=self._connected,
                card_present=self._card_present,
                firmware_version="C603_SIM_001",
                port=None,
                baud_rate=None,
            )

    def inspect_card(self) -> CardInspection:
        with self._lock:
            if not self._connected:
                self.connect()
            self._card_present = True
            component_hardware = DESFireVersionComponent(4, 1, 1, 0x33, 0, 0x1A, 5)
            component_software = DESFireVersionComponent(4, 1, 1, 3, 0, 0x1A, 5)
            if self.credential_application_id in self.application_ids:
                credential_state = "application_present"
            elif self.application_ids:
                credential_state = "foreign_or_unprovisioned"
            else:
                credential_state = "blank"
            return CardInspection(
                card_type="mifare_desfire_ev3",
                uid="04A1B2C3D4E5F6",
                rf_uid="04A1B2C3D4E5F6",
                atr="067577810280",
                hardware=component_hardware,
                software=component_software,
                batch_number="0102030405",
                production_week=1,
                production_year=2026,
                capacity_bytes=8192,
                free_memory_bytes=8192,
                application_ids=self.application_ids,
                credential_application_id=self.credential_application_id,
                credential_state=credential_state,
            )

    def probe_security(self) -> CardSecurityProbe:
        inspection = self.inspect_card()
        return CardSecurityProbe(
            uid=inspection.uid,
            picc_key_settings=0x0F,
            picc_key_count=1,
            picc_key_type="des_or_2k3des",
            factory_master_key_authenticated=True,
        )

    def plan_provisioning(
        self,
        *,
        expected_uid: str,
        credential_id: str,
        write_enabled: bool,
    ) -> CardProvisioningPlan:
        inspection = self.inspect_card()
        expected = expected_uid.strip().upper()
        if inspection.uid != expected:
            raise CardReaderProtocolError(
                f"El UID presente es {inspection.uid}; se esperaba {expected}."
            )
        normalized_credential_id = str(uuid.UUID(credential_id))
        blank = not inspection.application_ids
        return CardProvisioningPlan(
            uid=inspection.uid,
            application_id=self.credential_application_id,
            credential_id=normalized_credential_id,
            card_type=inspection.card_type,
            current_state=inspection.credential_state,
            will_create_aes_application=blank,
            will_create_credential_file=blank,
            will_personalize_keys=blank,
            write_enabled=write_enabled,
        )

    def provision_card(
        self,
        *,
        expected_uid: str,
        credential_id: str,
        master_key: bytes,
    ) -> CardProvisioningResult:
        with self._lock:
            plan = self.plan_provisioning(
                expected_uid=expected_uid,
                credential_id=credential_id,
                write_enabled=True,
            )
            if len(master_key) != 16:
                raise ValueError("La clave maestra de aprovisionamiento debe tener 16 bytes.")
            if self.application_ids:
                raise CardReaderProtocolError(
                    "La tarjeta no está vacía; no se modificó. Usa otra tarjeta de prueba."
                )
            self.application_ids = (self.credential_application_id,)
            self._credential_id = plan.credential_id
            self._provisioning_master_key = master_key
            return CardProvisioningResult(
                uid=plan.uid,
                application_id=plan.application_id,
                credential_id=plan.credential_id,
                application_created=True,
                credential_file_created=True,
                keys_personalized=True,
                verified=True,
            )

    def verify_credential(self, *, master_key: bytes) -> CardCredentialVerification:
        inspection = self.inspect_card()
        if (
            self._credential_id is None
            or self._provisioning_master_key is None
            or master_key != self._provisioning_master_key
        ):
            raise CardReaderProtocolError(
                "La credencial simulada no existe o la clave maestra no coincide."
            )
        return CardCredentialVerification(
            uid=inspection.uid,
            application_id=self.credential_application_id,
            credential_id=self._credential_id,
            authenticated=True,
            credential_version=1,
        )
