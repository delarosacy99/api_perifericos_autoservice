from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.stations.urls")),
    path("api/v1/", include("apps.cash_acceptor.urls")),
    path("api/v1/", include("apps.card_reader.urls")),
    path("api/v1/", include("apps.door_monitor.urls")),
    path("api/v1/", include("apps.printer_monitor.urls")),
]
