import ctypes
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .base import (
    PrinterMonitorConfigurationError,
    PrinterMonitorConnectionError,
    PrinterMonitorDriver,
    PrinterMonitorProtocolError,
    PrinterObservation,
)
from .status import MAPPING_SOURCE, decode_full_status

CESMLM_ERROR_MESSAGES = {
    50: "función no disponible en esta impresora",
    100: "no se pudo abrir un recurso del registro de Windows",
    110: "no se encontró la clave de registro de la impresora",
    145: "parámetro incorrecto",
    210: "no se seleccionó un dispositivo USB",
    230: "no se pudo abrir la tubería USB",
    240: "el SDK devolvió un identificador USB inválido",
    250: "la lectura USB quedó incompleta",
    260: "la escritura USB quedó incompleta",
    270: "error del sistema USB",
    280: "no hay una impresora USB conectada",
    290: "falló la detección del dispositivo USB",
    300: "no se encontraron los datos de la impresora seleccionada",
    330: "la tubería USB está ocupada",
    340: "recursos USB insuficientes",
    350: "índice de dispositivo USB fuera de rango",
    360: "respuesta USB desconocida",
    420: "la impresora no ejecutó correctamente el comando de consulta",
}


def _error_detail(library_error: int, system_error: int) -> str:
    detail = CESMLM_ERROR_MESSAGES.get(library_error, "error no documentado por CeSmLm")
    return f"CeSmLm={library_error} ({detail}), Windows={system_error}"


class CeSmLmClient(Protocol):
    def initialize_usb(self) -> tuple[int, int]: ...

    def get_interface_number(self, printer_name: str) -> tuple[int, int]: ...

    def get_full_gsi(self, interface_number: int) -> tuple[int, int, int]: ...

    def set_printer_model_gsi(self, interface_number: int, gsi: int) -> int: ...

    def get_status(self, interface_number: int) -> tuple[int, int, int]: ...

    def get_remaining_paper_cm(self, interface_number: int) -> tuple[int, int, int]: ...


class CtypesCeSmLmClient:
    def __init__(self, dll_path: str = "") -> None:
        if sys.platform != "win32":
            raise PrinterMonitorConfigurationError(
                "El driver CUSTOM K80 CeSmLm solamente puede ejecutarse en Windows."
            )
        resolved_path = Path(dll_path) if dll_path else self.default_dll_path()
        if not resolved_path.is_file():
            raise PrinterMonitorConfigurationError(
                f"No se encontró CeSMLm.dll en {resolved_path}. "
                "Instala el monitor de estado incluido con el driver CUSTOM K80 o configura "
                "CUSTOM_K80_CESMLM_DLL_PATH."
            )
        try:
            self._dll = ctypes.WinDLL(str(resolved_path))
        except OSError as exc:
            raise PrinterMonitorConfigurationError(
                f"Windows no pudo cargar CeSMLm.dll desde {resolved_path}: {exc}"
            ) from exc
        self.dll_path = str(resolved_path)
        self._bind_functions()

    @staticmethod
    def default_dll_path() -> Path:
        windows_directory = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return windows_directory / "System32" / "CeSMLm.dll"

    def _bind_functions(self) -> None:
        dword_pointer = ctypes.POINTER(ctypes.c_uint32)
        signatures = {
            "CePrnInitCeUsbSI": ([dword_pointer], ctypes.c_uint32),
            "CePrnGetInterfaceNumUsb": ([ctypes.c_char_p, dword_pointer], ctypes.c_uint32),
            "CePrnGetFullGsiUsb": (
                [ctypes.c_int, dword_pointer, dword_pointer],
                ctypes.c_uint32,
            ),
            "SetUsbPrinterModelGSI": ([ctypes.c_int, ctypes.c_uint32], ctypes.c_uint32),
            "CePrnGetStsUsb": (
                [ctypes.c_int, dword_pointer, dword_pointer],
                ctypes.c_uint32,
            ),
            "CePrnGetTotPaperRemainingUsb": (
                [ctypes.c_int, dword_pointer, dword_pointer],
                ctypes.c_uint32,
            ),
        }
        for name, (argtypes, restype) in signatures.items():
            try:
                function = getattr(self._dll, name)
            except AttributeError as exc:
                raise PrinterMonitorConfigurationError(
                    f"La biblioteca CeSMLm.dll no exporta la función requerida {name}."
                ) from exc
            function.argtypes = argtypes
            function.restype = restype

    def initialize_usb(self) -> tuple[int, int]:
        system_error = ctypes.c_uint32()
        device_count = int(self._dll.CePrnInitCeUsbSI(ctypes.byref(system_error)))
        return device_count, int(system_error.value)

    def get_interface_number(self, printer_name: str) -> tuple[int, int]:
        interface_number = ctypes.c_uint32(127)
        encoded_name = printer_name.encode("mbcs")
        library_error = int(
            self._dll.CePrnGetInterfaceNumUsb(encoded_name, ctypes.byref(interface_number))
        )
        return library_error, int(interface_number.value)

    def get_full_gsi(self, interface_number: int) -> tuple[int, int, int]:
        gsi = ctypes.c_uint32()
        system_error = ctypes.c_uint32()
        library_error = int(
            self._dll.CePrnGetFullGsiUsb(
                interface_number,
                ctypes.byref(gsi),
                ctypes.byref(system_error),
            )
        )
        return library_error, int(system_error.value), int(gsi.value)

    def set_printer_model_gsi(self, interface_number: int, gsi: int) -> int:
        return int(self._dll.SetUsbPrinterModelGSI(interface_number, gsi))

    def get_status(self, interface_number: int) -> tuple[int, int, int]:
        status = ctypes.c_uint32()
        system_error = ctypes.c_uint32()
        library_error = int(
            self._dll.CePrnGetStsUsb(
                interface_number,
                ctypes.byref(status),
                ctypes.byref(system_error),
            )
        )
        return library_error, int(system_error.value), int(status.value)

    def get_remaining_paper_cm(self, interface_number: int) -> tuple[int, int, int]:
        remaining = ctypes.c_uint32()
        system_error = ctypes.c_uint32()
        library_error = int(
            self._dll.CePrnGetTotPaperRemainingUsb(
                interface_number,
                ctypes.byref(remaining),
                ctypes.byref(system_error),
            )
        )
        return library_error, int(system_error.value), int(remaining.value)


@dataclass(frozen=True, slots=True)
class CustomK80CeSmLmConfig:
    printer_name: str = "CUSTOM K80"
    dll_path: str = ""
    query_remaining_paper: bool = True
    mapping_physically_validated: bool = False


class CustomK80CeSmLmDriver(PrinterMonitorDriver):
    name = "custom_k80_cesmlm"

    def __init__(
        self,
        config: CustomK80CeSmLmConfig,
        *,
        client: CeSmLmClient | None = None,
    ) -> None:
        if not config.printer_name.strip():
            raise PrinterMonitorConfigurationError(
                "CUSTOM_K80_PRINTER_NAME no puede estar vacío."
            )
        self.config = config
        self._client = client
        self._interface_number: int | None = None
        self._model_gsi: int | None = None
        self._lock = threading.RLock()

    def _get_client(self) -> CeSmLmClient:
        if self._client is None:
            self._client = CtypesCeSmLmClient(self.config.dll_path)
        return self._client

    def _initialize(self) -> None:
        if self._interface_number is not None and self._model_gsi is not None:
            return
        client = self._get_client()
        device_count, system_error = client.initialize_usb()
        if system_error or device_count < 1:
            raise PrinterMonitorConnectionError(
                "CeSmLm no detectó ninguna impresora CUSTOM por USB "
                f"(dispositivos={device_count}, Windows={system_error})."
            )

        library_error, interface_number = client.get_interface_number(
            self.config.printer_name
        )
        if library_error or interface_number == 127:
            raise PrinterMonitorConnectionError(
                f"CeSmLm no encontró la cola {self.config.printer_name!r}: "
                f"{_error_detail(library_error, 0)}."
            )

        library_error, system_error, model_gsi = client.get_full_gsi(interface_number)
        if library_error or system_error:
            raise PrinterMonitorProtocolError(
                "No se pudo identificar el modelo de la impresora: "
                f"{_error_detail(library_error, system_error)}."
            )
        library_error = client.set_printer_model_gsi(interface_number, model_gsi)
        if library_error:
            raise PrinterMonitorProtocolError(
                "CeSmLm no pudo seleccionar el modelo por GSI: "
                f"{_error_detail(library_error, 0)}."
            )
        self._interface_number = interface_number
        self._model_gsi = model_gsi

    def read_status(self) -> PrinterObservation:
        with self._lock:
            self._initialize()
            client = self._get_client()
            library_error, system_error, raw_status = client.get_status(
                self._interface_number
            )
            if library_error or system_error:
                self.disconnect()
                raise PrinterMonitorConnectionError(
                    "No se pudo leer el estado de la CUSTOM K80: "
                    f"{_error_detail(library_error, system_error)}."
                )

            decoded = decode_full_status(raw_status)
            paper_remaining_cm = None
            paper_remaining_error = None
            if self.config.query_remaining_paper and decoded.paper_near_end:
                remaining_error, remaining_system_error, remaining_cm = (
                    client.get_remaining_paper_cm(self._interface_number)
                )
                if not remaining_error and not remaining_system_error:
                    paper_remaining_cm = remaining_cm
                else:
                    paper_remaining_error = _error_detail(
                        remaining_error,
                        remaining_system_error,
                    )

            return PrinterObservation(
                device_id="printer",
                driver=self.name,
                model="CUSTOM K80",
                printer_name=self.config.printer_name,
                connected=True,
                monitoring_only=True,
                state=decoded.state,
                ready=decoded.ready,
                alerts=decoded.alerts,
                paper_remaining_cm=paper_remaining_cm,
                paper_remaining_estimated=paper_remaining_cm is not None,
                paper_remaining_error=paper_remaining_error,
                raw_status=raw_status,
                raw_status_hex=f"{raw_status:08X}",
                status_bytes=decoded.status_bytes,
                interface_number=self._interface_number,
                model_gsi_hex=f"{self._model_gsi:08X}",
                mapping_source=MAPPING_SOURCE,
                mapping_physically_validated=self.config.mapping_physically_validated,
                **{
                    field: getattr(decoded, field)
                    for field in (
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
                },
            )

    def disconnect(self) -> None:
        with self._lock:
            self._interface_number = None
            self._model_gsi = None

