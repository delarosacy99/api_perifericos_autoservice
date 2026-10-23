from abc import ABC, abstractmethod
from dataclasses import dataclass


class CashAcceptorError(Exception):
    code = "cash_acceptor_error"


class CashAcceptorNotConnected(CashAcceptorError):
    code = "cash_acceptor_not_connected"


class CashAcceptorNotAccepting(CashAcceptorError):
    code = "cash_acceptor_not_accepting"


class CashAcceptorConnectionError(CashAcceptorError):
    code = "cash_acceptor_connection_error"


class CashAcceptorProtocolError(CashAcceptorError):
    code = "cash_acceptor_protocol_error"


class UnsupportedDenomination(CashAcceptorError):
    code = "unsupported_denomination"


@dataclass(frozen=True, slots=True)
class CashChannel:
    channel: int
    currency: str
    denomination_minor: int
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class CashAcceptorSnapshot:
    driver: str
    model: str
    serial_number: str
    connected: bool
    accepting: bool
    dataset_version: str
    firmware_version: str
    channels: tuple[CashChannel, ...]
    cashbox_present: bool | None = None
    alerts: tuple[str, ...] = ()

    @property
    def currencies(self) -> set[str]:
        return {channel.currency for channel in self.channels}


@dataclass(frozen=True, slots=True)
class NoteCreditEvent:
    event_id: str
    channel: int
    currency: str
    denomination_minor: int
    raw_event: dict


class CashAcceptorDriver(ABC):
    supports_polling = False

    @abstractmethod
    def connect(self) -> CashAcceptorSnapshot:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> CashAcceptorSnapshot:
        raise NotImplementedError

    @abstractmethod
    def enable_currency(
        self,
        currency: str,
        denominations_minor: set[int] | None = None,
    ) -> tuple[int, ...]:
        raise NotImplementedError

    @abstractmethod
    def disable(self) -> None:
        raise NotImplementedError
