import atexit
import os
import sys
from pathlib import Path

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_asgi_application()


def _is_server_process() -> bool:
    executable_name = Path(sys.argv[0]).name.lower()
    if executable_name in {"manage.py", "django-admin", "django-admin.exe"}:
        return len(sys.argv) > 1 and sys.argv[1] == "runserver"
    return True


if settings.DOOR_MONITOR_BACKGROUND_ENABLED and _is_server_process():
    from apps.door_monitor.runtime import get_door_monitor_poller

    door_monitor_poller = get_door_monitor_poller()
    door_monitor_poller.ensure_running()
    atexit.register(door_monitor_poller.stop)
