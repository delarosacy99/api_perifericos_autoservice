from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView


class StationConfigurationView(APIView):
    def get(self, request):
        return Response(
            {
                "station_id": settings.STATION_ID,
                "country_code": settings.STATION_COUNTRY_CODE,
                "timezone": settings.STATION_TIMEZONE,
                "cash": {
                    "driver": settings.CASH_ACCEPTOR_DRIVER,
                    "currency": settings.CASH_CURRENCY,
                    "expected_dataset_currency": settings.CASH_EXPECTED_DATASET_CURRENCY,
                    "accept_all_dataset_denominations": (
                        settings.CASH_ACCEPT_ALL_DATASET_DENOMINATIONS
                    ),
                    "accepted_denominations_minor": (settings.CASH_ACCEPTED_DENOMINATIONS_MINOR),
                },
                "card_reader": {
                    "driver": settings.CARD_READER_DRIVER,
                    "model": (
                        "CRT-603"
                        if settings.CARD_READER_DRIVER == "crt603_serial"
                        else "CRT-603-SIMULATOR"
                    ),
                    "port": settings.CRT603_COM_PORT or None,
                    "baud_rate": settings.CRT603_BAUD_RATE,
                    "credential_application_id": settings.CARD_CREDENTIAL_APPLICATION_ID,
                },
                "door": {
                    "id": "front",
                    "name": "Puerta frontal",
                    "coverage": "front_door_only",
                    "driver": settings.DOOR_MONITOR_DRIVER,
                    "model": (
                        "XL128 V1.2"
                        if settings.DOOR_MONITOR_DRIVER == "xl128_serial"
                        else "XL128-V1.2-SIMULATOR"
                    ),
                    "port": settings.XL128_COM_PORT or None,
                    "baud_rate": settings.XL128_BAUD_RATE,
                    "monitoring_only": True,
                    "background_monitoring_enabled": (settings.DOOR_MONITOR_BACKGROUND_ENABLED),
                    "poll_interval_seconds": settings.DOOR_MONITOR_POLL_INTERVAL_SECONDS,
                    "debounce_samples": settings.DOOR_MONITOR_DEBOUNCE_SAMPLES,
                    "state_mapping_configured": bool(
                        settings.XL128_DOOR_OPEN_RESPONSE_CODES
                        and settings.XL128_DOOR_CLOSED_RESPONSE_CODES
                    ),
                },
                "printer": {
                    "output_provider": "jsprintmanager" if settings.BRIDGE_ENABLED else None,
                    "jsprintmanager_printer_name": settings.JSPM_PRINTER_NAME,
                    "driver": settings.PRINTER_MONITOR_DRIVER,
                    "model": (
                        "CUSTOM K80"
                        if settings.PRINTER_MONITOR_DRIVER
                        in {"custom_k80_cesmlm", "custom_k80_ceprintlib"}
                        else "CUSTOM-K80-SIMULATOR"
                    ),
                    "printer_name": settings.CUSTOM_K80_PRINTER_NAME,
                    "interface": "usb",
                    "monitoring_only": True,
                    "printing_enabled": settings.BRIDGE_ENABLED,
                    "remaining_paper_query_enabled": (
                        settings.CUSTOM_K80_QUERY_REMAINING_PAPER
                        and settings.PRINTER_MONITOR_DRIVER == "custom_k80_cesmlm"
                    ),
                    "remaining_paper_estimation_supported": (
                        settings.PRINTER_MONITOR_DRIVER == "custom_k80_cesmlm"
                    ),
                    "status_mapping_physically_validated": (
                        settings.CUSTOM_K80_STATUS_MAPPING_VALIDATED
                    ),
                },
                "capabilities": {
                    "frontend_bridge": settings.BRIDGE_ENABLED,
                    "websocket_events": settings.BRIDGE_ENABLED,
                    "central_sync": settings.BRIDGE_ENABLED,
                    "cash_collection": True,
                    "rfid_membership": False,
                    "card_inspection": True,
                    "card_credential_provisioning": False,
                    "card_credential_verification": True,
                    "card_credential_verification_configured": bool(
                        settings.CARD_PROVISIONING_MASTER_KEY_HEX
                    ),
                    "door_monitoring": True,
                    "door_actuation": False,
                    "printer_monitoring": True,
                    "printer_output": settings.BRIDGE_ENABLED,
                    "session_inactivity_timeout": settings.BRIDGE_ENABLED,
                    "business_rules": False,
                    "financial_ledger": False,
                },
            }
        )
