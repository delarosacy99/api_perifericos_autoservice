from django.apps import AppConfig


class BridgeConfig(AppConfig):
    name = "apps.bridge"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401
