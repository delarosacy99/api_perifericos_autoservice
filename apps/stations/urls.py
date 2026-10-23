from django.urls import path

from .views import StationConfigurationView

urlpatterns = [
    path(
        "station/configuration/",
        StationConfigurationView.as_view(),
        name="station-configuration",
    ),
]
