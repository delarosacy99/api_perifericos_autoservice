from django.urls import path

from .views import DoorStatusView

urlpatterns = [
    path(
        "devices/door/",
        DoorStatusView.as_view(),
        name="door-status",
    ),
]
