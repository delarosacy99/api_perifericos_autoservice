import ctypes
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .base import (
    PrinterMonitorConfigurationError,
    PrinterMonitorConnectionError,
    PrinterMonitorDriver,
    PrinterObservation,
)
from .status import (
    CEPRINTLIB_K80_MAPPING_SOURCE,
    decode_ceprintlib_k80_status,
)

CEPRINTLIB_ERROR_MESSAGES = {
    10: "no se pudo abrir el recurso o dispositivo",
    20: "identificador de dispositivo inválido",
    30: "no se pudo cerrar el recurso",
    40: "tamaño de búfer inválido",
    50: "falló la escritura",
    60: "falló la lectura",
    70: "error interno de la biblioteca",
    80: "falló una función de Windows",
    85: "falló el acceso al registro de Windows",
    90: "búfer vacío",
    95: "búfer insuficiente",
    100: "parámetro inválido",
    110: "contexto USB inválido",
    120: "no se detectó un dispositivo USB",
    130: "no se detectó una impresora",
    140: "la impresora está fuera de línea",
    150: "venció el tiempo de espera de lectura",
    160: "venció el tiempo de espera de escritura",
    170: "página de códigos no encontrada",
    171: "página de códigos no cargada",
    172: "carácter no disponible en la página de códigos",
    173: "error de conversión de página de códigos",
}


def _error_detail(library_error: int, system_error: int) -> str:
    detail = CEPRINTLIB_ERROR_MESSAGES.get(
        library_error,
        "error no documentado por CePrintLib",
    )
    return f"CePrintLib={library_error} ({detail}), Windows={system_error}"


class CePrintLibClient(Protocol):
    def initialize_usb(self) -> tuple[int, int, int | None]: ...

    def open_usb_device(self, context: int, printer_name: str) -> tuple[int, int]: ...

    def get_status(self, context: int) -> tuple[int, int, int]: ...

    def deinitialize_usb(self, context: int) -> int: ...


class CtypesCePrintLibClient:
    def __init__(self, dll_path: str) -> None:
        if sys.platform != "win32":
            raise PrinterMonitorConfigurationError(
                "El driver CUSTOM K80 CePrintLib solamente puede ejecutarse en Windows."
            )
        resolved_path = Path(dll_path)
        if not resolved_path.is_file():
            raise PrinterMonitorConfigurationError(
                f"No se encontró CePrnLib.dll en {resolved_path}. "
                "Vuelve a copiar la carpeta vendor incluida en el proyecto."
            )
        try:
            self._dll = ctypes.CDLL(str(resolved_path))
        except OSError as exc:
            raise PrinterMonitorConfigurationError(
                f"Windows no pudo cargar CePrnLib.dll desde {resolved_path}: {exc}"
            ) from exc
        self.dll_path = str(resolved_path)
        self._bind_functions()

    def _bind_functions(self) -> None:
        dword_pointer = ctypes.POINTER(ctypes.c_uint32)
        try:
            self._dll.CeInitUSBLayer.argtypes = [
                ctypes.POINTER(ctypes.c_void_p),
                dword_pointer,
            ]
            self._dll.CeInitUSBLayer.restype = ctypes.c_uint32
            self._dll.CeOpenUSBDev.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                dword_pointer,
            ]
            self._dll.CeOpenUSBDev.restype = ctypes.c_uint32
            self._dll.CeGetSts.argtypes = [
                ctypes.c_void_p,
                dword_pointer,
                dword_pointer,
            ]
            self._dll.CeGetSts.restype = ctypes.c_uint32
            self._dll.CeDeInitUSBLayer.argtypes = [ctypes.c_void_p]
            self._dll.CeDeInitUSBLayer.restype = ctypes.c_uint32
        except AttributeError as exc:
            raise PrinterMonitorConfigurationError(
                "CePrnLib.dll no contiene todas las funciones USB requeridas."
            ) from exc

    def initialize_usb(self) -> tuple[int, int, int | None]:
        context = ctypes.c_void_p()
        system_error = ctypes.c_uint32()
        library_error = int(
            self._dll.CeInitUSBLayer(
                ctypes.byref(context),
                ctypes.byref(system_error),
            )
        )
        return library_error, int(system_error.value), context.value

    def open_usb_device(self, context: int, printer_name: str) -> tuple[int, int]:
        system_error = ctypes.c_uint32()
        library_error = int(
            self._dll.CeOpenUSBDev(
                ctypes.c_void_p(context),
                printer_name,
                ctypes.byref(system_error),
            )
        )
        return library_error, int(system_error.value)

    def get_status(self, context: int) -> tuple[int, int, int]:
        status = ctypes.c_uint32()
        system_error = ctypes.c_uint32()
        library_error = int(
            self._dll.CeGetSts(
                ctypes.c_void_p(context),
                ctypes.byref(status),
                ctypes.byref(system_error),
            )
        )
        return library_error, int(system_error.value), int(status.value)

    def deinitialize_usb(self, context: int) -> int:
        return int(self._dll.CeDeInitUSBLayer(ctypes.c_void_p(context)))


@dataclass(frozen=True, slots=True)
class CustomK80CePrintLibConfig:
    printer_name: str = "CUSTOM K80"
    dll_path: str = ""
    mapping_physically_validated: bool = False


class CustomK80CePrintLibDriver(PrinterMonitorDriver):
    name = "custom_k80_ceprintlib"

    def __init__(
        self,
        config: CustomK80CePrintLibConfig,
        *,
        client: CePrintLibClient | None = None,
    ) -> None:
        if not config.printer_name.strip():
            raise PrinterMonitorConfigurationError(
                "CUSTOM_K80_PRINTER_NAME no puede estar vacío."
            )
        if client is None and not config.dll_path:
            raise PrinterMonitorConfigurationError(
                "CUSTOM_K80_CEPRNLIB_DLL_PATH no puede estar vacío."
            )
        self.config = config
        self._client = client
        self._context: int | None = None
        self._lock = threading.RLock()

    def _get_client(self) -> CePrintLibClient:
        if self._client is None:
            self._client = CtypesCePrintLibClient(self.config.dll_path)
        return self._client

    def _initialize(self) -> None:
        if self._context is not None:
            return
        client = self._get_client()
        library_error, system_error, context = client.initialize_usb()
        if library_error or system_error or context is None:
            raise PrinterMonitorConnectionError(
                "No se pudo inicializar la capa USB de la CUSTOM K80: "
                f"{_error_detail(library_error, system_error)}."
            )
        library_error, system_error = client.open_usb_device(
            context,
            self.config.printer_name,
        )
        if library_error or system_error:
            client.deinitialize_usb(context)
            raise PrinterMonitorConnectionError(
                f"No se pudo abrir la cola {self.config.printer_name!r} por USB: "
                f"{_error_detail(library_error, system_error)}."
            )
        self._context = context

    def read_status(self) -> PrinterObservation:
        with self._lock:
            self._initialize()
            library_error, system_error, raw_status = self._get_client().get_status(
                self._context
            )
            if library_error or system_error:
                self.disconnect()
                raise PrinterMonitorConnectionError(
                    "No se pudo leer el estado de la CUSTOM K80: "
                    f"{_error_detail(library_error, system_error)}."
                )

            decoded = decode_ceprintlib_k80_status(raw_status)
            remaining_error = (
                "CePrintLib 1.19 no expone la estimación de papel restante en centímetros."
                if decoded.paper_near_end
                else None
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
                paper_remaining_cm=None,
                paper_remaining_estimated=False,
                paper_remaining_error=remaining_error,
                raw_status=raw_status,
                raw_status_hex=f"{raw_status:08X}",
                status_bytes=decoded.status_bytes,
                interface_number=None,
                model_gsi_hex=None,
                mapping_source=CEPRINTLIB_K80_MAPPING_SOURCE,
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
            if self._context is not None:
                try:
                    self._get_client().deinitialize_usb(self._context)
                finally:
                    self._context = None

