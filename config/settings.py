import os
import re
import struct
from pathlib import Path

from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_int_csv(name: str, default: str = "") -> list[int]:
    values = env_csv(name, default)
    try:
        parsed = [int(value) for value in values]
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} debe contener enteros separados por coma") from exc
    if any(value <= 0 for value in parsed):
        raise ImproperlyConfigured(f"{name} solamente acepta valores mayores que cero")
    return parsed


def env_hex_byte_csv(name: str, default: str = "") -> list[int]:
    values = env_csv(name, default)
    try:
        parsed = [int(value.removeprefix("0x").removeprefix("0X"), 16) for value in values]
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{name} debe contener bytes hexadecimales separados por coma"
        ) from exc
    if any(not 0 <= value <= 0xFF for value in parsed):
        raise ImproperlyConfigured(f"{name} solamente acepta bytes entre 00 y FF")
    if len(set(parsed)) != len(parsed):
        raise ImproperlyConfigured(f"{name} no debe contener valores repetidos")
    return parsed


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)), 0)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} debe ser un número entero") from exc


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} debe ser un número decimal") from exc


APP_NAME = "api_perifericos_autoservice"
APP_VERSION = "0.8.7"

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_csv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")

LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", "development-local-api-key" if DEBUG else "")
if not DEBUG and not LOCAL_API_KEY:
    raise ImproperlyConfigured("LOCAL_API_KEY es obligatorio cuando DJANGO_DEBUG=false")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "channels",
    "apps.core",
    "apps.stations",
    "apps.outbox",
    "apps.cash_acceptor",
    "apps.card_reader",
    "apps.door_monitor",
    "apps.printer_monitor",
    "apps.station_cash",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "var" / "api_perifericos_autoservice.sqlite3",
        "OPTIONS": {"timeout": 5},
    }
}

LANGUAGE_CODE = "es-do"
TIME_ZONE = os.getenv("STATION_TIMEZONE", "America/Santo_Domingo")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.core.authentication.LocalAPIKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "UNAUTHENTICATED_USER": None,
}

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env_csv("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_HEADERS = (*default_headers, "x-local-api-key", "idempotency-key")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

STATION_ID = os.getenv("STATION_ID", "AUTOSERVICE-DEV-001")
CENTRAL_SYNC_ENABLED = env_bool("CENTRAL_SYNC_ENABLED", False)
CENTRAL_API_BASE_URL = os.getenv("CENTRAL_API_BASE_URL", "").strip()
CENTRAL_AGENT_CREDENTIAL = os.getenv("CENTRAL_AGENT_CREDENTIAL", "").strip()
CENTRAL_TIMEOUT_SECONDS = env_float("CENTRAL_TIMEOUT_SECONDS", 8.0)
FRONTEND_HEARTBEAT_TIMEOUT_SECONDS = env_int("FRONTEND_HEARTBEAT_TIMEOUT_SECONDS", 20)
if CENTRAL_SYNC_ENABLED and (not CENTRAL_API_BASE_URL or not CENTRAL_AGENT_CREDENTIAL):
    raise ImproperlyConfigured("Configure CENTRAL_API_BASE_URL y CENTRAL_AGENT_CREDENTIAL")
if CENTRAL_TIMEOUT_SECONDS <= 0 or FRONTEND_HEARTBEAT_TIMEOUT_SECONDS < 10:
    raise ImproperlyConfigured("Tiempos de sincronización inválidos")
STATION_COUNTRY_CODE = os.getenv("STATION_COUNTRY_CODE", "DO").upper()
STATION_TIMEZONE = TIME_ZONE

CASH_ACCEPTOR_DRIVER = os.getenv("CASH_ACCEPTOR_DRIVER", "simulator").lower()
if CENTRAL_SYNC_ENABLED and not DEBUG and CASH_ACCEPTOR_DRIVER == "simulator":
    raise ImproperlyConfigured("La sincronización productiva requiere billetero real")
CASH_CURRENCY = os.getenv("CASH_CURRENCY", "USD").upper()
CASH_EXPECTED_DATASET_CURRENCY = os.getenv("CASH_EXPECTED_DATASET_CURRENCY", CASH_CURRENCY).upper()
CASH_ACCEPT_ALL_DATASET_DENOMINATIONS = env_bool("CASH_ACCEPT_ALL_DATASET_DENOMINATIONS", True)
CASH_ACCEPTED_DENOMINATIONS_MINOR = env_int_csv(
    "CASH_ACCEPTED_DENOMINATIONS_MINOR",
    "100,200,500,1000,2000,5000,10000",
)
CASH_SIMULATOR_DATASET_CURRENCY = os.getenv(
    "CASH_SIMULATOR_DATASET_CURRENCY", CASH_EXPECTED_DATASET_CURRENCY
).upper()
CASH_SIMULATOR_DATASET_VERSION = os.getenv("CASH_SIMULATOR_DATASET_VERSION", "USD-SIM-001")
CASH_SIMULATOR_FIRMWARE_VERSION = os.getenv("CASH_SIMULATOR_FIRMWARE_VERSION", "NV200-SIM-001")

NV200_COM_PORT = os.getenv("NV200_COM_PORT", "").strip()
NV200_BAUD_RATE = env_int("NV200_BAUD_RATE", 9600)
NV200_SSP_ADDRESS = env_int("NV200_SSP_ADDRESS", 0)
NV200_COMMAND_TIMEOUT_SECONDS = env_float("NV200_COMMAND_TIMEOUT_SECONDS", 1.0)
NV200_WRITE_TIMEOUT_SECONDS = env_float("NV200_WRITE_TIMEOUT_SECONDS", 0.5)
NV200_COMMAND_ATTEMPTS = env_int("NV200_COMMAND_ATTEMPTS", 3)
NV200_MIN_PROTOCOL_VERSION = env_int("NV200_MIN_PROTOCOL_VERSION", 6)
NV200_MAX_PROTOCOL_VERSION = env_int("NV200_MAX_PROTOCOL_VERSION", 20)
NV200_FIXED_KEY = env_int("NV200_FIXED_KEY", 0x0123456701234567)
NV200_POLL_WITH_ACK = env_bool("NV200_POLL_WITH_ACK", True)
if CENTRAL_SYNC_ENABLED and not NV200_POLL_WITH_ACK:
    raise ImproperlyConfigured("La sincronización requiere NV200_POLL_WITH_ACK=true")
NV200_POLL_INTERVAL_SECONDS = env_float("NV200_POLL_INTERVAL_SECONDS", 0.2)

CARD_READER_DRIVER = os.getenv("CARD_READER_DRIVER", "simulator").lower()
CARD_CREDENTIAL_APPLICATION_ID = os.getenv("CARD_CREDENTIAL_APPLICATION_ID", "D3E001").upper()
CARD_ALLOW_TEST_PROVISIONING = env_bool("CARD_ALLOW_TEST_PROVISIONING", False)
CARD_PROVISIONING_MASTER_KEY_HEX = os.getenv("CARD_PROVISIONING_MASTER_KEY_HEX", "").strip().upper()
CRT603_COM_PORT = os.getenv("CRT603_COM_PORT", "").strip()
CRT603_BAUD_RATE = env_int("CRT603_BAUD_RATE", 19200)
CRT603_COMMAND_TIMEOUT_SECONDS = env_float("CRT603_COMMAND_TIMEOUT_SECONDS", 1.0)
CRT603_WRITE_TIMEOUT_SECONDS = env_float("CRT603_WRITE_TIMEOUT_SECONDS", 0.5)
CRT603_COMMAND_ATTEMPTS = env_int("CRT603_COMMAND_ATTEMPTS", 2)

DOOR_MONITOR_DRIVER = os.getenv("DOOR_MONITOR_DRIVER", "simulator").lower()
DOOR_SIMULATOR_STATE = os.getenv("DOOR_SIMULATOR_STATE", "closed").lower()
XL128_COM_PORT = os.getenv("XL128_COM_PORT", "").strip()
XL128_BAUD_RATE = env_int("XL128_BAUD_RATE", 9600)
XL128_COMMAND_TIMEOUT_SECONDS = env_float("XL128_COMMAND_TIMEOUT_SECONDS", 1.0)
XL128_WRITE_TIMEOUT_SECONDS = env_float("XL128_WRITE_TIMEOUT_SECONDS", 0.5)
XL128_COMMAND_ATTEMPTS = env_int("XL128_COMMAND_ATTEMPTS", 2)
XL128_DOOR_OPEN_RESPONSE_CODES = env_hex_byte_csv("XL128_DOOR_OPEN_RESPONSE_CODES")
XL128_DOOR_CLOSED_RESPONSE_CODES = env_hex_byte_csv("XL128_DOOR_CLOSED_RESPONSE_CODES")
DOOR_MONITOR_BACKGROUND_ENABLED = env_bool("DOOR_MONITOR_BACKGROUND_ENABLED", False)
DOOR_MONITOR_POLL_INTERVAL_SECONDS = env_float("DOOR_MONITOR_POLL_INTERVAL_SECONDS", 0.5)
DOOR_MONITOR_DEBOUNCE_SAMPLES = env_int("DOOR_MONITOR_DEBOUNCE_SAMPLES", 2)
DOOR_MONITOR_OFFLINE_AFTER_FAILURES = env_int("DOOR_MONITOR_OFFLINE_AFTER_FAILURES", 3)
DOOR_MONITOR_RECOVERY_SAMPLES = env_int("DOOR_MONITOR_RECOVERY_SAMPLES", 2)

PRINTER_MONITOR_DRIVER = os.getenv("PRINTER_MONITOR_DRIVER", "simulator").lower()
PRINTER_SIMULATOR_RAW_STATUS = env_int("PRINTER_SIMULATOR_RAW_STATUS", 0)
_printer_simulator_remaining = os.getenv("PRINTER_SIMULATOR_PAPER_REMAINING_CM", "").strip()
PRINTER_SIMULATOR_PAPER_REMAINING_CM = (
    env_int("PRINTER_SIMULATOR_PAPER_REMAINING_CM", 0)
    if _printer_simulator_remaining
    else None
)
CUSTOM_K80_PRINTER_NAME = os.getenv("CUSTOM_K80_PRINTER_NAME", "CUSTOM K80").strip()
CUSTOM_K80_CESMLM_DLL_PATH = os.getenv("CUSTOM_K80_CESMLM_DLL_PATH", "").strip()
_ceprintlib_architecture = "x64" if struct.calcsize("P") == 8 else "win32"
_ceprintlib_configured_path = os.getenv("CUSTOM_K80_CEPRNLIB_DLL_PATH", "").strip()
CUSTOM_K80_CEPRNLIB_DLL_PATH = _ceprintlib_configured_path or str(
    BASE_DIR / "vendor" / "custom_k80" / _ceprintlib_architecture / "CePrnLib.dll"
)
CUSTOM_K80_QUERY_REMAINING_PAPER = env_bool("CUSTOM_K80_QUERY_REMAINING_PAPER", True)
CUSTOM_K80_STATUS_MAPPING_VALIDATED = env_bool(
    "CUSTOM_K80_STATUS_MAPPING_VALIDATED", False
)

if not 0 <= NV200_SSP_ADDRESS <= 0x7D:
    raise ImproperlyConfigured("NV200_SSP_ADDRESS debe estar entre 0 y 125")
if NV200_COMMAND_ATTEMPTS < 1:
    raise ImproperlyConfigured("NV200_COMMAND_ATTEMPTS debe ser al menos 1")
if NV200_POLL_INTERVAL_SECONDS <= 0:
    raise ImproperlyConfigured("NV200_POLL_INTERVAL_SECONDS debe ser mayor que cero")
if NV200_MIN_PROTOCOL_VERSION < 6:
    raise ImproperlyConfigured("NV200_MIN_PROTOCOL_VERSION debe ser 6 o superior")
if NV200_MAX_PROTOCOL_VERSION < NV200_MIN_PROTOCOL_VERSION:
    raise ImproperlyConfigured(
        "NV200_MAX_PROTOCOL_VERSION no puede ser menor que NV200_MIN_PROTOCOL_VERSION"
    )
if CARD_READER_DRIVER not in {"simulator", "crt603_serial"}:
    raise ImproperlyConfigured("CARD_READER_DRIVER debe ser simulator o crt603_serial")
if not re.fullmatch(r"[0-9A-F]{6}", CARD_CREDENTIAL_APPLICATION_ID):
    raise ImproperlyConfigured(
        "CARD_CREDENTIAL_APPLICATION_ID debe contener exactamente 6 caracteres hexadecimales"
    )
if CARD_PROVISIONING_MASTER_KEY_HEX and not re.fullmatch(
    r"[0-9A-F]{32}", CARD_PROVISIONING_MASTER_KEY_HEX
):
    raise ImproperlyConfigured(
        "CARD_PROVISIONING_MASTER_KEY_HEX debe contener 32 caracteres hexadecimales"
    )
if CRT603_BAUD_RATE not in {9600, 19200, 38400, 115200}:
    raise ImproperlyConfigured("CRT603_BAUD_RATE debe ser 9600, 19200, 38400 o 115200")
if CRT603_COMMAND_TIMEOUT_SECONDS <= 0:
    raise ImproperlyConfigured("CRT603_COMMAND_TIMEOUT_SECONDS debe ser mayor que cero")
if CRT603_COMMAND_ATTEMPTS < 1:
    raise ImproperlyConfigured("CRT603_COMMAND_ATTEMPTS debe ser al menos 1")
if DOOR_MONITOR_DRIVER not in {"simulator", "xl128_serial"}:
    raise ImproperlyConfigured("DOOR_MONITOR_DRIVER debe ser simulator o xl128_serial")
if DOOR_SIMULATOR_STATE not in {"open", "closed"}:
    raise ImproperlyConfigured("DOOR_SIMULATOR_STATE debe ser open o closed")
if XL128_BAUD_RATE != 9600:
    raise ImproperlyConfigured("XL128_BAUD_RATE debe ser 9600 según el SDK del XL128")
if XL128_COMMAND_TIMEOUT_SECONDS <= 0:
    raise ImproperlyConfigured("XL128_COMMAND_TIMEOUT_SECONDS debe ser mayor que cero")
if XL128_WRITE_TIMEOUT_SECONDS <= 0:
    raise ImproperlyConfigured("XL128_WRITE_TIMEOUT_SECONDS debe ser mayor que cero")
if XL128_COMMAND_ATTEMPTS < 1:
    raise ImproperlyConfigured("XL128_COMMAND_ATTEMPTS debe ser al menos 1")
if set(XL128_DOOR_OPEN_RESPONSE_CODES) & set(XL128_DOOR_CLOSED_RESPONSE_CODES):
    raise ImproperlyConfigured(
        "Un código XL128 no puede representar puerta abierta y cerrada al mismo tiempo"
    )
if DOOR_MONITOR_POLL_INTERVAL_SECONDS <= 0:
    raise ImproperlyConfigured("DOOR_MONITOR_POLL_INTERVAL_SECONDS debe ser mayor que cero")
if DOOR_MONITOR_DEBOUNCE_SAMPLES < 1:
    raise ImproperlyConfigured("DOOR_MONITOR_DEBOUNCE_SAMPLES debe ser al menos 1")
if DOOR_MONITOR_OFFLINE_AFTER_FAILURES < 1:
    raise ImproperlyConfigured("DOOR_MONITOR_OFFLINE_AFTER_FAILURES debe ser al menos 1")
if DOOR_MONITOR_RECOVERY_SAMPLES < 1:
    raise ImproperlyConfigured("DOOR_MONITOR_RECOVERY_SAMPLES debe ser al menos 1")
if PRINTER_MONITOR_DRIVER not in {
    "simulator",
    "custom_k80_cesmlm",
    "custom_k80_ceprintlib",
}:
    raise ImproperlyConfigured(
        "PRINTER_MONITOR_DRIVER debe ser simulator, custom_k80_cesmlm "
        "o custom_k80_ceprintlib"
    )
if not 0 <= PRINTER_SIMULATOR_RAW_STATUS <= 0xFFFFFFFF:
    raise ImproperlyConfigured("PRINTER_SIMULATOR_RAW_STATUS debe ser un DWORD sin signo")
if (
    PRINTER_SIMULATOR_PAPER_REMAINING_CM is not None
    and PRINTER_SIMULATOR_PAPER_REMAINING_CM < 0
):
    raise ImproperlyConfigured(
        "PRINTER_SIMULATOR_PAPER_REMAINING_CM no puede ser negativo"
    )
if not CUSTOM_K80_PRINTER_NAME:
    raise ImproperlyConfigured("CUSTOM_K80_PRINTER_NAME no puede estar vacío")
if PRINTER_MONITOR_DRIVER == "custom_k80_ceprintlib" and not CUSTOM_K80_CEPRNLIB_DLL_PATH:
    raise ImproperlyConfigured(
        "CUSTOM_K80_CEPRNLIB_DLL_PATH es obligatorio con custom_k80_ceprintlib"
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{levelname} {asctime} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        }
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
