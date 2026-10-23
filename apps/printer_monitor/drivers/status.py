from dataclasses import dataclass

# Máscaras publicadas por CUSTOM en los ejemplos de CeSmLm 4.06A para FULL STATUS
# (DLE EOT 0x14). El propio SDK exige confirmar la correspondencia con el manual
# de comandos de cada modelo; por eso la API también expone el DWORD y sus bytes.
NO_PAPER = 0x00000001
NEAR_PAPER_END = 0x00000004
PAPER_END = 0x00000010
TICKET_OUT = 0x00000020
VIRTUAL_PAPER_END = 0x00000040
PRINT_HEAD_UP = 0x00000100
COVER_OPEN = 0x00000200
OVER_TEMPERATURE = 0x00010000
COMMAND_ERROR = 0x00200000
PAPER_JAM = 0x00400000
CUTTER_ERROR = 0x01000000

MAPPING_SOURCE = "custom_cesmlm_4_06a_generic_full_status_sample"
CEPRINTLIB_K80_MAPPING_SOURCE = "custom_k80_commands_manual_77200000003300"


@dataclass(frozen=True, slots=True)
class DecodedPrinterStatus:
    state: str
    ready: bool
    alerts: tuple[str, ...]
    paper_out: bool
    paper_near_end: bool
    paper_end: bool
    virtual_paper_end: bool
    ticket_out: bool
    print_head_up: bool
    print_head_up_or_paper_jam: bool
    cover_open: bool
    paper_jam: bool
    cutter_error: bool
    over_temperature: bool
    command_error: bool
    status_bytes: dict[str, str]


def _has(status: int, mask: int) -> bool:
    return (status & mask) == mask


def decode_full_status(status: int) -> DecodedPrinterStatus:
    if not 0 <= status <= 0xFFFFFFFF:
        raise ValueError("El estado de impresora debe ser un DWORD sin signo.")

    no_paper = _has(status, NO_PAPER)
    paper_end = _has(status, PAPER_END)
    values = {
        "paper_out": no_paper or paper_end,
        "paper_near_end": _has(status, NEAR_PAPER_END),
        "paper_end": paper_end,
        "virtual_paper_end": _has(status, VIRTUAL_PAPER_END),
        "ticket_out": _has(status, TICKET_OUT),
        "print_head_up": _has(status, PRINT_HEAD_UP),
        "print_head_up_or_paper_jam": False,
        "cover_open": _has(status, COVER_OPEN),
        "paper_jam": _has(status, PAPER_JAM),
        "cutter_error": _has(status, CUTTER_ERROR),
        "over_temperature": _has(status, OVER_TEMPERATURE),
        "command_error": _has(status, COMMAND_ERROR),
    }
    alert_order = (
        "paper_out",
        "paper_near_end",
        "paper_end",
        "virtual_paper_end",
        "ticket_out",
        "print_head_up",
        "print_head_up_or_paper_jam",
        "cover_open",
        "paper_jam",
        "cutter_error",
        "over_temperature",
        "command_error",
    )
    alerts = tuple(name for name in alert_order if values[name])
    blocking = any(
        values[name]
        for name in (
            "paper_out",
            "paper_end",
            "virtual_paper_end",
            "print_head_up",
            "print_head_up_or_paper_jam",
            "cover_open",
            "paper_jam",
            "cutter_error",
            "over_temperature",
            "command_error",
        )
    )
    state = "error" if blocking else "warning" if alerts else "ready"
    return DecodedPrinterStatus(
        state=state,
        ready=not blocking,
        alerts=alerts,
        status_bytes={
            "paper_status_hex": f"{status & 0xFF:02X}",
            "user_status_hex": f"{(status >> 8) & 0xFF:02X}",
            "recoverable_error_status_hex": f"{(status >> 16) & 0xFF:02X}",
            "unrecoverable_error_status_hex": f"{(status >> 24) & 0xFF:02X}",
        },
        **values,
    )


def decode_ceprintlib_k80_status(status: int) -> DecodedPrinterStatus:
    """Decode the K80 PP-UU-RR-NN DWORD documented for DLE EOT 20."""
    if not 0 <= status <= 0xFFFFFFFF:
        raise ValueError("El estado de impresora debe ser un DWORD sin signo.")

    paper_status = (status >> 24) & 0xFF
    user_status = (status >> 16) & 0xFF
    recoverable_status = (status >> 8) & 0xFF
    unrecoverable_status = status & 0xFF
    values = {
        "paper_out": bool(paper_status & 0x01),
        "paper_near_end": bool(paper_status & 0x04),
        "paper_end": False,
        "virtual_paper_end": False,
        "ticket_out": bool(paper_status & 0x20),
        "print_head_up": False,
        "print_head_up_or_paper_jam": bool(user_status & 0x01),
        "cover_open": bool(user_status & 0x02),
        "paper_jam": False,
        "cutter_error": bool(unrecoverable_status & 0x01),
        "over_temperature": bool(recoverable_status & 0x01),
        "command_error": bool(recoverable_status & 0x20),
    }
    alert_order = (
        "paper_out",
        "paper_near_end",
        "ticket_out",
        "print_head_up_or_paper_jam",
        "cover_open",
        "cutter_error",
        "over_temperature",
        "command_error",
    )
    alerts = tuple(name for name in alert_order if values[name])
    blocking = any(
        values[name]
        for name in (
            "paper_out",
            "print_head_up_or_paper_jam",
            "cover_open",
            "cutter_error",
            "over_temperature",
            "command_error",
        )
    )
    return DecodedPrinterStatus(
        state="error" if blocking else "warning" if alerts else "ready",
        ready=not blocking,
        alerts=alerts,
        status_bytes={
            "paper_status_hex": f"{paper_status:02X}",
            "user_status_hex": f"{user_status:02X}",
            "recoverable_error_status_hex": f"{recoverable_status:02X}",
            "unrecoverable_error_status_hex": f"{unrecoverable_status:02X}",
        },
        **values,
    )

