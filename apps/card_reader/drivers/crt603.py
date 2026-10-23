import hmac
import threading
import uuid

from .base import (
    CardCredentialVerification,
    CardInspection,
    CardProvisioningPlan,
    CardProvisioningResult,
    CardReaderDriver,
    CardReaderNoCard,
    CardReaderNotConnected,
    CardReaderProtocolError,
    CardReaderSnapshot,
    CardSecurityProbe,
    DESFireStatusError,
    UnsupportedCard,
)
from .crt603_protocol import CRT603CommandError, CRT603SerialConfig, CRT603SerialTransport
from .desfire import (
    APDU_ADDITIONAL_FRAME,
    APDU_GET_APPLICATION_IDS,
    APDU_GET_FREE_MEMORY,
    APDU_GET_VERSION,
    STATUS_ADDITIONAL_FRAME,
    STATUS_OK,
    card_type_from_software,
    decode_bcd,
    decode_storage_size,
    parse_desfire_status,
    parse_version_component,
)
from .desfire_security import (
    STATUS_OK as APDU_STATUS_OK,
)
from .desfire_security import (
    aes_cmac_with_iv,
    authenticate_aes,
    authenticate_iso_2k3des,
    change_aes_key,
    derive_card_key,
    require_status,
    split_native_response,
    wrap_native_apdu,
)

CMD_GET_FIRMWARE = 0x03
CMD_SET_CARD_MODE = 0x70
CMD_SEEK_CARD = 0x20
CMD_TYPE_A_RESET = 0x30
CMD_SEND_APDU = 0x31

APPLICATION_KEY_SETTINGS = 0x09
APPLICATION_AES_KEY_COUNT = 2
CREDENTIAL_FILE_NUMBER = 0
CREDENTIAL_FILE_SIZE = 17
CREDENTIAL_FORMAT_VERSION = 1


class CRT603SerialDriver(CardReaderDriver):
    name = "crt603_serial"

    def __init__(
        self,
        config: CRT603SerialConfig,
        *,
        credential_application_id: str,
        transport: CRT603SerialTransport | None = None,
    ) -> None:
        self.config = config
        self.credential_application_id = credential_application_id.upper()
        self._transport = transport or CRT603SerialTransport(config)
        self._lock = threading.RLock()
        self._connected = False
        self._card_present = False
        self._firmware_version = ""

    def connect(self) -> CardReaderSnapshot:
        with self._lock:
            if self._connected and self._transport.is_open:
                return self.snapshot()
            try:
                self._transport.open()
                firmware = self._transport.exchange(CMD_GET_FIRMWARE)
            except Exception:
                self._connected = False
                self._transport.close()
                raise
            self._firmware_version = firmware.decode("ascii", errors="replace").strip("\x00 ")
            self._connected = True
            return self.snapshot()

    def disconnect(self) -> None:
        with self._lock:
            self._transport.close()
            self._connected = False
            self._card_present = False

    def snapshot(self) -> CardReaderSnapshot:
        with self._lock:
            connected = self._connected and self._transport.is_open
            return CardReaderSnapshot(
                driver=self.name,
                model="CRT-603",
                connected=connected,
                card_present=self._card_present if connected else False,
                firmware_version=self._firmware_version,
                port=self.config.port,
                baud_rate=self.config.baud_rate,
            )

    def _ensure_connected(self) -> None:
        if not self._connected or not self._transport.is_open:
            self.connect()
        if not self._connected:
            raise CardReaderNotConnected("El CRT-603 no está conectado.")

    def _send_apdu(self, apdu: bytes) -> bytes:
        return self._transport.exchange(CMD_SEND_APDU, apdu)

    def _send_desfire_command(self, apdu: bytes) -> tuple[bytes, ...]:
        frames: list[bytes] = []
        current_apdu = apdu
        for _frame_number in range(16):
            response = self._send_apdu(current_apdu)
            data, status = parse_desfire_status(response)
            frames.append(data)
            if status == STATUS_OK:
                return tuple(frames)
            if status != STATUS_ADDITIONAL_FRAME:
                raise DESFireStatusError(status.hex().upper())
            current_apdu = APDU_ADDITIONAL_FRAME
        raise CardReaderProtocolError("La tarjeta DESFire excedió 16 tramas de continuación.")

    @staticmethod
    def _extract_rf_uid(seek_data: bytes) -> str:
        if len(seek_data) < 7:
            raise CardReaderProtocolError("La respuesta Seek Card del CRT-603 es inválida.")
        uid_length = len(seek_data) - 3
        if uid_length not in {4, 7, 10}:
            raise CardReaderProtocolError(
                "La longitud del UID devuelto por Seek Card no es válida."
            )
        return seek_data[:uid_length].hex().upper()

    def inspect_card(self) -> CardInspection:
        with self._lock:
            self._ensure_connected()
            try:
                self._transport.exchange(CMD_SET_CARD_MODE, b"\x00")
                seek_data = self._transport.exchange(CMD_SEEK_CARD, b"\x00")
                atr = self._transport.exchange(CMD_TYPE_A_RESET)
            except CRT603CommandError as exc:
                self._card_present = False
                if exc.command in {CMD_SEEK_CARD, CMD_TYPE_A_RESET}:
                    raise CardReaderNoCard(
                        "No hay una tarjeta ISO14443-4 Type A activa sobre el lector."
                    ) from exc
                raise

            self._card_present = True
            rf_uid = self._extract_rf_uid(seek_data)

            version_frames = self._send_desfire_command(APDU_GET_VERSION)
            if len(version_frames) < 3:
                raise UnsupportedCard("GetVersion no devolvió los tres bloques DESFire.")
            hardware = parse_version_component(version_frames[0])
            software = parse_version_component(version_frames[1])
            manufacturing = version_frames[2]
            if len(manufacturing) < 14:
                raise UnsupportedCard("El bloque de fabricación DESFire está incompleto.")

            uid_bytes = manufacturing[:7]
            batch_number = manufacturing[7:12].hex().upper()
            production_week = decode_bcd(manufacturing[12], field="semana")
            production_year = 2000 + decode_bcd(manufacturing[13], field="año")

            application_data = b"".join(self._send_desfire_command(APDU_GET_APPLICATION_IDS))
            if len(application_data) % 3:
                raise CardReaderProtocolError(
                    "GetApplicationIDs devolvió una longitud que no es múltiplo de 3."
                )
            application_ids = tuple(
                application_data[offset : offset + 3].hex().upper()
                for offset in range(0, len(application_data), 3)
            )

            free_memory_data = b"".join(self._send_desfire_command(APDU_GET_FREE_MEMORY))
            if len(free_memory_data) != 3:
                raise CardReaderProtocolError("GetFreeMemory no devolvió exactamente 3 bytes.")
            free_memory_bytes = int.from_bytes(free_memory_data, "little")

            if self.credential_application_id in application_ids:
                credential_state = "application_present"
            elif application_ids:
                credential_state = "foreign_or_unprovisioned"
            else:
                credential_state = "blank"

            return CardInspection(
                card_type=card_type_from_software(software),
                uid=uid_bytes.hex().upper(),
                rf_uid=rf_uid,
                atr=atr.hex().upper(),
                hardware=hardware,
                software=software,
                batch_number=batch_number,
                production_week=production_week,
                production_year=production_year,
                capacity_bytes=decode_storage_size(hardware.storage_size_code),
                free_memory_bytes=free_memory_bytes,
                application_ids=application_ids,
                credential_application_id=self.credential_application_id,
                credential_state=credential_state,
            )

    def probe_security(self) -> CardSecurityProbe:
        with self._lock:
            inspection = self.inspect_card()
            key_settings_data = require_status(
                self._send_apdu(wrap_native_apdu(0x45)),
                APDU_STATUS_OK,
            )
            if len(key_settings_data) != 2:
                raise CardReaderProtocolError("GetKeySettings no devolvió exactamente 2 bytes.")
            key_settings, key_count_and_type = key_settings_data
            key_type_bits = key_count_and_type & 0xC0
            key_types = {
                0x00: "des_or_2k3des",
                0x40: "3k3des",
                0x80: "aes128",
            }
            key_type = key_types.get(key_type_bits, "reserved")
            authenticated = False
            if key_type == "des_or_2k3des":
                authenticate_iso_2k3des(self._send_apdu)
                authenticated = True
            return CardSecurityProbe(
                uid=inspection.uid,
                picc_key_settings=key_settings,
                picc_key_count=key_count_and_type & 0x0F,
                picc_key_type=key_type,
                factory_master_key_authenticated=authenticated,
            )

    @staticmethod
    def _normalize_uid(value: str) -> str:
        normalized = value.strip().upper()
        try:
            parsed = bytes.fromhex(normalized)
        except ValueError as exc:
            raise ValueError("El UID debe ser hexadecimal.") from exc
        if len(parsed) not in {4, 7, 10}:
            raise ValueError("El UID debe contener 4, 7 o 10 bytes.")
        return normalized

    @staticmethod
    def _normalize_credential_id(value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except (AttributeError, ValueError) as exc:
            raise ValueError("credential_id debe ser un UUID válido.") from exc

    def _validated_provisioning_inspection(
        self,
        *,
        expected_uid: str,
    ) -> CardInspection:
        expected = self._normalize_uid(expected_uid)
        inspection = self.inspect_card()
        if inspection.uid != expected:
            raise CardReaderProtocolError(
                f"El UID presente es {inspection.uid}; se esperaba {expected}."
            )
        if inspection.card_type != "mifare_desfire_ev3":
            raise UnsupportedCard(
                "El aprovisionamiento de prueba solamente admite MIFARE DESFire EV3."
            )
        return inspection

    def plan_provisioning(
        self,
        *,
        expected_uid: str,
        credential_id: str,
        write_enabled: bool,
    ) -> CardProvisioningPlan:
        with self._lock:
            inspection = self._validated_provisioning_inspection(
                expected_uid=expected_uid,
            )
            normalized_credential_id = self._normalize_credential_id(credential_id)
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

    def _select_application(self, application_id: bytes) -> None:
        require_status(
            self._send_apdu(wrap_native_apdu(0x5A, application_id)),
            APDU_STATUS_OK,
        )

    def _authenticate_application_key(
        self,
        *,
        application_id: bytes,
        key_number: int,
        key: bytes,
    ):
        self._select_application(application_id)
        return authenticate_aes(
            self._send_apdu,
            key_number=key_number,
            key=key,
        )

    def _create_credential_application(self, application_id: bytes) -> None:
        payload = application_id + bytes(
            (APPLICATION_KEY_SETTINGS, 0x80 | APPLICATION_AES_KEY_COUNT)
        )
        require_status(
            self._send_apdu(wrap_native_apdu(0xCA, payload)),
            APDU_STATUS_OK,
        )

    def _create_and_write_credential_file(self, credential_id: str) -> None:
        access_rights = 0x0010
        create_payload = (
            bytes((CREDENTIAL_FILE_NUMBER, 0x00))
            + access_rights.to_bytes(2, "little")
            + CREDENTIAL_FILE_SIZE.to_bytes(3, "little")
        )
        require_status(
            self._send_apdu(wrap_native_apdu(0xCD, create_payload)),
            APDU_STATUS_OK,
        )
        credential_payload = bytes((CREDENTIAL_FORMAT_VERSION,)) + uuid.UUID(credential_id).bytes
        write_payload = (
            bytes((CREDENTIAL_FILE_NUMBER,))
            + bytes(3)
            + len(credential_payload).to_bytes(3, "little")
            + credential_payload
        )
        require_status(
            self._send_apdu(wrap_native_apdu(0x3D, write_payload)),
            APDU_STATUS_OK,
        )

    def _read_credential(
        self,
        *,
        session_key: bytes,
    ) -> tuple[int, str]:
        read_parameters = (
            bytes((CREDENTIAL_FILE_NUMBER,)) + bytes(3) + CREDENTIAL_FILE_SIZE.to_bytes(3, "little")
        )
        native_command = bytes((0xBD,)) + read_parameters
        command_cmac = aes_cmac_with_iv(
            session_key,
            bytes(16),
            native_command,
        )
        response = self._send_apdu(wrap_native_apdu(0xBD, read_parameters))
        response_data, status = split_native_response(response)
        if status != APDU_STATUS_OK:
            raise DESFireStatusError(status.hex().upper())
        if len(response_data) != CREDENTIAL_FILE_SIZE + 8:
            raise CardReaderProtocolError(
                "ReadData no devolvió la credencial y su CMAC de 8 bytes."
            )
        credential_payload = response_data[:CREDENTIAL_FILE_SIZE]
        received_cmac = response_data[CREDENTIAL_FILE_SIZE:]
        expected_cmac = aes_cmac_with_iv(
            session_key,
            command_cmac,
            credential_payload + b"\x00",
        )[:8]
        if not hmac.compare_digest(received_cmac, expected_cmac):
            raise CardReaderProtocolError(
                "La respuesta de la credencial no superó la verificación CMAC."
            )
        version = credential_payload[0]
        if version != CREDENTIAL_FORMAT_VERSION:
            raise CardReaderProtocolError(f"La versión de credencial {version} no es compatible.")
        return version, str(uuid.UUID(bytes=credential_payload[1:]))

    def provision_card(
        self,
        *,
        expected_uid: str,
        credential_id: str,
        master_key: bytes,
    ) -> CardProvisioningResult:
        with self._lock:
            if len(master_key) != 16:
                raise ValueError("La clave maestra de aprovisionamiento debe tener 16 bytes.")
            inspection = self._validated_provisioning_inspection(
                expected_uid=expected_uid,
            )
            normalized_credential_id = self._normalize_credential_id(credential_id)
            if inspection.application_ids:
                raise CardReaderProtocolError(
                    "La tarjeta no está vacía; no se modificó. Usa otra tarjeta de prueba."
                )

            application_id = bytes.fromhex(self.credential_application_id)
            uid = bytes.fromhex(inspection.uid)
            admin_key = derive_card_key(
                master_key,
                uid=uid,
                application_id=application_id,
                key_number=0,
            )
            authentication_key = derive_card_key(
                master_key,
                uid=uid,
                application_id=application_id,
                key_number=1,
            )

            authenticate_iso_2k3des(self._send_apdu)
            self._create_credential_application(application_id)

            self._authenticate_application_key(
                application_id=application_id,
                key_number=0,
                key=bytes(16),
            )
            self._create_and_write_credential_file(normalized_credential_id)

            key_one_session = self._authenticate_application_key(
                application_id=application_id,
                key_number=0,
                key=bytes(16),
            )
            change_aes_key(
                self._send_apdu,
                authenticated_key_number=0,
                target_key_number=1,
                session_key=key_one_session.session_key,
                old_key=bytes(16),
                new_key=authentication_key,
            )

            key_zero_session = self._authenticate_application_key(
                application_id=application_id,
                key_number=0,
                key=bytes(16),
            )
            change_aes_key(
                self._send_apdu,
                authenticated_key_number=0,
                target_key_number=0,
                session_key=key_zero_session.session_key,
                old_key=bytes(16),
                new_key=admin_key,
            )

            verification = self._verify_selected_credential(
                uid=inspection.uid,
                application_id=application_id,
                authentication_key=authentication_key,
            )
            if verification.credential_id != normalized_credential_id:
                raise CardReaderProtocolError(
                    "La credencial leída no coincide con la credencial escrita."
                )
            return CardProvisioningResult(
                uid=inspection.uid,
                application_id=self.credential_application_id,
                credential_id=normalized_credential_id,
                application_created=True,
                credential_file_created=True,
                keys_personalized=True,
                verified=True,
            )

    def _verify_selected_credential(
        self,
        *,
        uid: str,
        application_id: bytes,
        authentication_key: bytes,
    ) -> CardCredentialVerification:
        session = self._authenticate_application_key(
            application_id=application_id,
            key_number=1,
            key=authentication_key,
        )
        version, credential_id = self._read_credential(
            session_key=session.session_key,
        )
        return CardCredentialVerification(
            uid=uid,
            application_id=application_id.hex().upper(),
            credential_id=credential_id,
            authenticated=True,
            credential_version=version,
        )

    def verify_credential(self, *, master_key: bytes) -> CardCredentialVerification:
        with self._lock:
            if len(master_key) != 16:
                raise ValueError("La clave maestra de aprovisionamiento debe tener 16 bytes.")
            inspection = self.inspect_card()
            if self.credential_application_id not in inspection.application_ids:
                raise CardReaderProtocolError(
                    f"La tarjeta no contiene la aplicación {self.credential_application_id}."
                )
            application_id = bytes.fromhex(self.credential_application_id)
            authentication_key = derive_card_key(
                master_key,
                uid=bytes.fromhex(inspection.uid),
                application_id=application_id,
                key_number=1,
            )
            return self._verify_selected_credential(
                uid=inspection.uid,
                application_id=application_id,
                authentication_key=authentication_key,
            )
