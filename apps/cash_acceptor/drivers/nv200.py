import logging
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from .base import (
    CashAcceptorConnectionError,
    CashAcceptorDriver,
    CashAcceptorNotAccepting,
    CashAcceptorNotConnected,
    CashAcceptorProtocolError,
    CashAcceptorSnapshot,
    CashChannel,
    NoteCreditEvent,
)
from .essp import (
    SerialSSPTransport,
    SSPClient,
    SSPError,
    SSPResponseError,
    SSPSerialConfig,
)

logger = logging.getLogger(__name__)

CMD_SET_INHIBITS = 0x02
CMD_SETUP_REQUEST = 0x05
CMD_HOST_PROTOCOL_VERSION = 0x06
CMD_POLL = 0x07
CMD_DISABLE = 0x09
CMD_ENABLE = 0x0A
CMD_GET_SERIAL_NUMBER = 0x0C
CMD_GET_FIRMWARE_VERSION = 0x20
CMD_GET_DATASET_VERSION = 0x21
CMD_POLL_WITH_ACK = 0x56
CMD_EVENT_ACK = 0x57

RESPONSE_OK = 0xF0
RESPONSE_FAIL = 0xF8

POLL_EVENT_DEFINITIONS = {
    0xB5: ("CHANNELS_DISABLED", 0),
    0xB6: ("INITIALISING", 0),
    0xCC: ("NOTE_STACKING", 0),
    0xD1: ("BARCODE_TICKET_ACK", 0),
    0xE0: ("NOTE_PATH_OPEN", 0),
    0xE1: ("NOTE_CLEARED_FROM_FRONT", 1),
    0xE2: ("NOTE_CLEARED_TO_CASHBOX", 1),
    0xE3: ("CASHBOX_REMOVED", 0),
    0xE4: ("CASHBOX_REPLACED", 0),
    0xE5: ("BARCODE_TICKET_VALIDATED", 0),
    0xE6: ("FRAUD_ATTEMPT", 1),
    0xE7: ("STACKER_FULL", 0),
    0xE8: ("DISABLED", 0),
    0xE9: ("UNSAFE_NOTE_JAM", 0),
    0xEA: ("SAFE_NOTE_JAM", 0),
    0xEB: ("NOTE_STACKED", 0),
    0xEC: ("NOTE_REJECTED", 0),
    0xED: ("NOTE_REJECTING", 0),
    0xEE: ("NOTE_CREDIT", 1),
    0xEF: ("NOTE_READ", 1),
    0xF1: ("SLAVE_RESET", 0),
}


@dataclass(frozen=True, slots=True)
class NV200Config:
    port: str
    baud_rate: int = 9600
    address: int = 0
    timeout_seconds: float = 1.0
    write_timeout_seconds: float = 0.5
    attempts: int = 3
    minimum_protocol_version: int = 6
    maximum_protocol_version: int = 20
    fixed_key: int = SSPClient.DEFAULT_FIXED_KEY
    poll_with_ack: bool = True
    journal_path: str = ""


@dataclass(frozen=True, slots=True)
class NV200Setup:
    unit_type: int
    firmware_version: str
    protocol_version: int
    channels: tuple[CashChannel, ...]


@dataclass(frozen=True, slots=True)
class NV200PollEvent:
    code: int
    name: str
    data: bytes


@dataclass(frozen=True, slots=True)
class NV200PollBatch:
    batch_id: str
    events: tuple[NV200PollEvent, ...]
    note_credits: tuple[NoteCreditEvent, ...]
    requires_ack: bool
    state_changes: tuple[str, ...] = ()


def parse_setup_response(response: bytes) -> NV200Setup:
    if not response or response[0] != RESPONSE_OK:
        raise CashAcceptorProtocolError("SETUP REQUEST no devolvió OK.")
    data = response[1:]
    if len(data) < 16:
        raise CashAcceptorProtocolError("SETUP REQUEST devolvió datos incompletos.")

    unit_type = data[0]
    firmware_raw = data[1:5].decode("ascii", errors="replace")
    firmware_version = f"{firmware_raw[:2]}.{firmware_raw[2:]}"
    number_of_channels = data[11]
    index = 12 + (number_of_channels * 2)
    if len(data) < index + 4:
        raise CashAcceptorProtocolError("SETUP REQUEST no contiene todos los canales declarados.")

    real_value_multiplier = int.from_bytes(data[index : index + 3], "big")
    index += 3
    protocol_version = data[index]
    index += 1
    if protocol_version < 6:
        raise CashAcceptorProtocolError(
            f"El dataset reportó protocolo {protocol_version}; se requiere versión 6 o superior."
        )

    expanded_length = number_of_channels * 7
    if len(data) < index + expanded_length:
        raise CashAcceptorProtocolError(
            "SETUP REQUEST no contiene moneda y valor expandido para todos los canales."
        )

    currencies_start = index
    values_start = currencies_start + (number_of_channels * 3)
    channels = []
    for offset in range(number_of_channels):
        currency_offset = currencies_start + (offset * 3)
        value_offset = values_start + (offset * 4)
        currency = data[currency_offset : currency_offset + 3].decode("ascii", errors="replace")
        raw_value = int.from_bytes(data[value_offset : value_offset + 4], "little")
        channels.append(
            CashChannel(
                channel=offset + 1,
                currency=currency,
                denomination_minor=raw_value * real_value_multiplier,
            )
        )

    return NV200Setup(
        unit_type=unit_type,
        firmware_version=firmware_version,
        protocol_version=protocol_version,
        channels=tuple(channels),
    )


def parse_poll_response(response: bytes) -> tuple[NV200PollEvent, ...]:
    if not response or response[0] != RESPONSE_OK:
        raise CashAcceptorProtocolError("POLL no devolvió OK.")

    events = []
    index = 1
    while index < len(response):
        code = response[index]
        definition = POLL_EVENT_DEFINITIONS.get(code)
        if definition is None:
            raise CashAcceptorProtocolError(
                f"Evento POLL desconocido 0x{code:02X}; no se puede determinar su longitud."
            )
        name, data_length = definition
        end = index + 1 + data_length
        if end > len(response):
            raise CashAcceptorProtocolError(f"El evento {name} llegó incompleto.")
        events.append(NV200PollEvent(code=code, name=name, data=response[index + 1 : end]))
        index = end
    return tuple(events)


class NV200ESSPDriver(CashAcceptorDriver):
    name = "nv200_essp"
    supports_polling = True

    def __init__(self, config: NV200Config, *, client: SSPClient | None = None) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._client = client or SSPClient(
            SerialSSPTransport(
                SSPSerialConfig(
                    port=config.port,
                    baud_rate=config.baud_rate,
                    timeout_seconds=config.timeout_seconds,
                    write_timeout_seconds=config.write_timeout_seconds,
                    attempts=config.attempts,
                )
            ),
            address=config.address,
            fixed_key=config.fixed_key,
        )
        self._connected = False
        self._accepting = False
        self._serial_number = ""
        self._dataset_version = ""
        self._firmware_version = ""
        self._protocol_version = 0
        self._channels: tuple[CashChannel, ...] = ()
        self._pending_batch: NV200PollBatch | None = None
        from .journal import HardwareJournal

        self._journal = HardwareJournal(config.journal_path) if config.journal_path else None
        self._journal_error = None
        try:
            self._journal_data = self._journal.read() if self._journal else None
        except CashAcceptorProtocolError as exc:
            # Still connect and DISABLE the physical device before refusing recovery.
            self._journal_error = exc
            self._journal_data = None
        self._ack_uncertain = bool(self._journal_data and self._journal_data["stage"] == "ack_intent")
        if self._journal_data and self._journal_data["stage"] != "acked":
            try:
                data = self._journal_data
                self._serial_number = data["serial_number"]
                self._pending_batch = NV200PollBatch(
                    batch_id=data["batch_id"],
                    events=tuple(
                        NV200PollEvent(e["code"], e["name"], bytes.fromhex(e["data"]))
                        for e in data["events"]
                    ),
                    note_credits=tuple(NoteCreditEvent(**n) for n in data["notes"]),
                    requires_ack=True,
                )
            except (KeyError, TypeError, ValueError):
                self._journal_error = CashAcceptorProtocolError("El diario NV200 requiere revisión técnica.")
                self._pending_batch = None
        self._cashbox_present: bool | None = None
        self._active_alerts: set[str] = set()
        self.note_inflight = False

    def connect(self) -> CashAcceptorSnapshot:
        with self._lock:
            if self._connected:
                self._require_journal_safe()
                return self.snapshot()
            previous_serial_number = self._serial_number
            try:
                self._client.open()
                self._client.negotiate_keys()
                self._client.command(CMD_DISABLE)
                self._accepting = False
                self._require_journal_safe()
                self._protocol_version = self._select_highest_protocol()
                setup = parse_setup_response(self._client.command(CMD_SETUP_REQUEST))
                if setup.unit_type != 0x00:
                    unit_type = f"0x{setup.unit_type:02X}"
                    raise CashAcceptorProtocolError(
                        f"El equipo conectado no es un validador de billetes (tipo {unit_type})."
                    )

                serial_response = self._client.command(CMD_GET_SERIAL_NUMBER)
                if len(serial_response) < 5:
                    raise CashAcceptorProtocolError(
                        "La respuesta de número de serie está incompleta."
                    )
                serial_number = str(int.from_bytes(serial_response[1:5], "big"))
                if (
                    self._pending_batch is not None
                    and previous_serial_number
                    and serial_number != previous_serial_number
                ):
                    raise CashAcceptorProtocolError(
                        "El NV200 reconectado no coincide con el equipo que originó "
                        "el lote pendiente de confirmación."
                    )
                self._serial_number = serial_number
                self._firmware_version = self._read_ascii_command(
                    CMD_GET_FIRMWARE_VERSION,
                    fallback=setup.firmware_version,
                )
                self._dataset_version = self._read_ascii_command(CMD_GET_DATASET_VERSION)
                self._channels = setup.channels
                self._client.command(CMD_DISABLE)
                self._connected = True
                self._accepting = False
                return self.snapshot()
            except CashAcceptorProtocolError:
                self._safe_close(clear_pending=False)
                raise
            except SSPError as exc:
                self._safe_close(clear_pending=False)
                raise CashAcceptorConnectionError(
                    f"Falló la conexión eSSP con el NV200: {exc}"
                ) from exc

    def _require_journal_safe(self):
        if self._journal_error:
            raise self._journal_error
        if self._ack_uncertain:
            raise CashAcceptorProtocolError(
                "ACK NV200 incierto tras reinicio: conserve el diario y concilie con soporte antes de habilitar."
            )

    def disconnect(self) -> None:
        with self._lock:
            if self._connected:
                try:
                    self._client.command(CMD_DISABLE)
                except SSPError:
                    logger.warning("No se pudo deshabilitar el NV200 al cerrar.", exc_info=True)
            self._safe_close(clear_pending=True)

    def snapshot(self) -> CashAcceptorSnapshot:
        with self._lock:
            return CashAcceptorSnapshot(
                driver=self.name,
                model="NV200",
                serial_number=self._serial_number,
                connected=self._connected,
                accepting=self._accepting,
                dataset_version=self._dataset_version,
                firmware_version=self._firmware_version,
                channels=self._channels,
                cashbox_present=self._cashbox_present,
                alerts=tuple(sorted(self._active_alerts)),
            )

    def enable_currency(
        self,
        currency: str,
        denominations_minor: set[int] | None = None,
    ) -> tuple[int, ...]:
        with self._lock:
            self._require_journal_safe()
            self._require_connected()
            if self._cashbox_present is False:
                raise CashAcceptorProtocolError(
                    "No se puede habilitar el NV200 mientras el cashbox está retirado."
                )
            normalized_currency = currency.upper()
            enabled_channels = tuple(
                channel.channel
                for channel in self._channels
                if channel.currency == normalized_currency
                and (
                    denominations_minor is None or channel.denomination_minor in denominations_minor
                )
            )
            if not enabled_channels:
                return ()

            # NV200 accepts one to three inhibit bytes. Sending all three also works for NV200S.
            masks = bytearray(3)
            for channel_number in enabled_channels:
                byte_index = (channel_number - 1) // 8
                if byte_index >= len(masks):
                    raise CashAcceptorProtocolError(
                        f"El canal {channel_number} excede los 24 canales soportados por NV200."
                    )
                masks[byte_index] |= 1 << ((channel_number - 1) % 8)

            try:
                self._client.command(CMD_SET_INHIBITS, bytes(masks))
                self._client.command(CMD_ENABLE)
            except SSPError as exc:
                self._safe_close(clear_pending=False)
                raise CashAcceptorConnectionError(f"No se pudo habilitar el NV200: {exc}") from exc

            enabled_set = set(enabled_channels)
            self._channels = tuple(
                replace(channel, enabled=channel.channel in enabled_set)
                for channel in self._channels
            )
            self._accepting = True
            return enabled_channels

    def disable(self) -> None:
        with self._lock:
            if not self._connected:
                self._accepting = False
                return
            try:
                self._client.command(CMD_DISABLE)
            except SSPError as exc:
                self._safe_close(clear_pending=False)
                raise CashAcceptorConnectionError(
                    f"No se pudo deshabilitar el NV200: {exc}"
                ) from exc
            finally:
                self._accepting = False
                self._channels = tuple(
                    replace(channel, enabled=False) for channel in self._channels
                )

    def poll_once(self, *, allow_disabled: bool = False) -> NV200PollBatch:
        with self._lock:
            self._require_journal_safe()
            self._require_connected()
            if not self._accepting and not allow_disabled:
                raise CashAcceptorNotAccepting("El NV200 no está aceptando billetes.")
            if self._pending_batch is not None:
                return self._pending_batch

            command = CMD_POLL_WITH_ACK if self.config.poll_with_ack else CMD_POLL
            try:
                response = self._client.command(command)
            except SSPError as exc:
                self._safe_close(clear_pending=False)
                raise CashAcceptorConnectionError(f"Falló el POLL del NV200: {exc}") from exc
            events = parse_poll_response(response)
            batch_id = str(uuid.uuid4())
            received_at = datetime.now(UTC).isoformat()
            note_credits = []
            state_changes = []
            for event_index, event in enumerate(events):
                if event.name in {"NOTE_READ", "NOTE_STACKING", "NOTE_REJECTING"}:
                    self.note_inflight = True
                elif event.name in {
                    "NOTE_CREDIT",
                    "NOTE_STACKED",
                    "NOTE_REJECTED",
                    "NOTE_CLEARED_FROM_FRONT",
                    "NOTE_CLEARED_TO_CASHBOX",
                }:
                    self.note_inflight = False
                if event.name == "CASHBOX_REMOVED":
                    if self._cashbox_present is not False:
                        state_changes.append(event.name)
                    self._cashbox_present = False
                    self._active_alerts.add("cashbox_removed")
                    self._accepting = False
                    self._channels = tuple(
                        replace(channel, enabled=False) for channel in self._channels
                    )
                elif event.name == "CASHBOX_REPLACED":
                    if self._cashbox_present is not True:
                        state_changes.append(event.name)
                    self._cashbox_present = True
                    self._active_alerts.discard("cashbox_removed")
                if event.code != 0xEE:
                    continue
                channel_number = event.data[0]
                channel = next(
                    (item for item in self._channels if item.channel == channel_number),
                    None,
                )
                if channel is None:
                    raise CashAcceptorProtocolError(
                        f"NOTE CREDIT referencia el canal desconocido {channel_number}."
                    )
                note_credits.append(
                    NoteCreditEvent(
                        event_id=(f"nv200-{self._serial_number}-{batch_id}-{event_index}"),
                        channel=channel.channel,
                        currency=channel.currency,
                        denomination_minor=channel.denomination_minor,
                        raw_event={
                            "event": event.name,
                            "code": f"0x{event.code:02X}",
                            "channel": channel.channel,
                            "poll_batch_id": batch_id,
                            "received_at": received_at,
                            "source": self.name,
                        },
                    )
                )

            batch = NV200PollBatch(
                batch_id=batch_id,
                events=events,
                note_credits=tuple(note_credits),
                requires_ack=self.config.poll_with_ack and bool(note_credits),
                state_changes=tuple(state_changes),
            )
            if batch.requires_ack:
                if self._journal:
                    from dataclasses import asdict

                    self._journal_data = {
                        "stage": "pending",
                        "serial_number": self._serial_number,
                        "batch_id": batch.batch_id,
                        "events": [
                            {"code": e.code, "name": e.name, "data": e.data.hex()}
                            for e in batch.events
                        ],
                        "notes": [asdict(n) for n in batch.note_credits],
                    }
                    self._journal.write(self._journal_data)
                self._pending_batch = batch
            return batch

    def acknowledge_events(self, batch_id: str) -> None:
        with self._lock:
            self._require_journal_safe()
            if self._pending_batch is None:
                return
            if self._pending_batch.batch_id != batch_id:
                raise CashAcceptorProtocolError(
                    "El lote POLL pendiente no coincide con el ACK solicitado."
                )
            try:
                if self._journal:
                    self._journal_data["stage"] = "ack_intent"
                    self._journal.write(self._journal_data)
                self._client.command(CMD_EVENT_ACK)
                if self._journal:
                    self._journal_data["stage"] = "acked"
                    self._journal.write(self._journal_data)
            except Exception as exc:
                if self._journal:
                    self._ack_uncertain = True
                self._safe_close(clear_pending=False)
                if isinstance(exc, SSPError):
                    raise CashAcceptorConnectionError(f"Falló EVENT ACK del NV200: {exc}") from exc
                raise
            self._pending_batch = None

    def _select_highest_protocol(self) -> int:
        selected = 0
        for version in range(
            self.config.minimum_protocol_version,
            self.config.maximum_protocol_version + 1,
        ):
            response = self._client.command(
                CMD_HOST_PROTOCOL_VERSION,
                bytes((version,)),
                require_ok=False,
            )
            if response[0] == RESPONSE_OK:
                selected = version
                continue
            if response[0] == RESPONSE_FAIL:
                break
            raise SSPResponseError(response[0], response)
        if selected < self.config.minimum_protocol_version:
            raise CashAcceptorProtocolError(
                "El NV200 no soporta la versión mínima de protocolo configurada."
            )
        return selected

    def _read_ascii_command(self, command: int, *, fallback: str = "") -> str:
        response = self._client.command(command)
        value = response[1:].decode("ascii", errors="replace").strip("\x00 ")
        return value or fallback

    def _require_connected(self) -> None:
        if not self._connected:
            raise CashAcceptorNotConnected("El NV200 no está conectado.")

    def _safe_close(self, *, clear_pending: bool) -> None:
        try:
            self._client.close()
        except SSPError:
            logger.warning("No se pudo cerrar el transporte eSSP.", exc_info=True)
        self._connected = False
        self._accepting = False
        if clear_pending and not self._journal:
            self._pending_batch = None
        self._channels = tuple(replace(channel, enabled=False) for channel in self._channels)
