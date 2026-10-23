import threading
import uuid
from dataclasses import replace

from django.conf import settings

from .base import (
    CashAcceptorDriver,
    CashAcceptorNotAccepting,
    CashAcceptorNotConnected,
    CashAcceptorSnapshot,
    CashChannel,
    NoteCreditEvent,
    UnsupportedDenomination,
)


class SimulatedNV200Driver(CashAcceptorDriver):
    name = "simulator"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._connected = False
        self._accepting = False
        currency = settings.CASH_SIMULATOR_DATASET_CURRENCY
        self._channels = tuple(
            CashChannel(
                channel=index,
                currency=currency,
                denomination_minor=denomination_minor,
            )
            for index, denomination_minor in enumerate(
                settings.CASH_ACCEPTED_DENOMINATIONS_MINOR,
                start=1,
            )
        )

    def connect(self) -> CashAcceptorSnapshot:
        with self._lock:
            self._connected = True
            return self.snapshot()

    def disconnect(self) -> None:
        with self._lock:
            self._accepting = False
            self._connected = False
            self._channels = tuple(replace(channel, enabled=False) for channel in self._channels)

    def snapshot(self) -> CashAcceptorSnapshot:
        with self._lock:
            return CashAcceptorSnapshot(
                driver=self.name,
                model="NV200-SIMULATOR",
                serial_number="SIM-NV200-0001",
                connected=self._connected,
                accepting=self._accepting,
                dataset_version=settings.CASH_SIMULATOR_DATASET_VERSION,
                firmware_version=settings.CASH_SIMULATOR_FIRMWARE_VERSION,
                channels=self._channels,
                cashbox_present=True,
            )

    def enable_currency(
        self,
        currency: str,
        denominations_minor: set[int] | None = None,
    ) -> tuple[int, ...]:
        with self._lock:
            if not self._connected:
                raise CashAcceptorNotConnected("El NV200 simulado no está conectado.")

            normalized_currency = currency.upper()
            updated_channels = []
            enabled_channels = []
            for channel in self._channels:
                enabled = channel.currency == normalized_currency and (
                    denominations_minor is None or channel.denomination_minor in denominations_minor
                )
                updated_channels.append(replace(channel, enabled=enabled))
                if enabled:
                    enabled_channels.append(channel.channel)

            self._channels = tuple(updated_channels)
            self._accepting = bool(enabled_channels)
            return tuple(enabled_channels)

    def disable(self) -> None:
        with self._lock:
            self._accepting = False
            self._channels = tuple(replace(channel, enabled=False) for channel in self._channels)

    def simulate_note(self, *, currency: str, denomination_minor: int) -> NoteCreditEvent:
        with self._lock:
            if not self._connected:
                raise CashAcceptorNotConnected("El NV200 simulado no está conectado.")
            if not self._accepting:
                raise CashAcceptorNotAccepting("El NV200 simulado no está aceptando billetes.")

            normalized_currency = currency.upper()
            channel = next(
                (
                    item
                    for item in self._channels
                    if item.enabled
                    and item.currency == normalized_currency
                    and item.denomination_minor == denomination_minor
                ),
                None,
            )
            if channel is None:
                raise UnsupportedDenomination(
                    "La denominación "
                    f"{denomination_minor} {normalized_currency} no está habilitada."
                )

            event_id = f"sim-{uuid.uuid4()}"
            return NoteCreditEvent(
                event_id=event_id,
                channel=channel.channel,
                currency=channel.currency,
                denomination_minor=channel.denomination_minor,
                raw_event={
                    "event": "NOTE_CREDIT",
                    "source": "simulator",
                    "channel": channel.channel,
                },
            )
