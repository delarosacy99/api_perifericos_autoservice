from django.core.management.base import CommandError

from ...drivers.base import CardReaderConfigurationError
from ...services import get_card_master_key


def get_provisioning_master_key() -> bytes:
    try:
        return get_card_master_key()
    except CardReaderConfigurationError as exc:
        raise CommandError(str(exc)) from exc
