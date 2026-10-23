from django.urls import path

from .views import PrinterStatusView

urlpatterns = [
    path(
        "devices/printer/",
        PrinterStatusView.as_view(),
        name="printer-status",
    ),
]

