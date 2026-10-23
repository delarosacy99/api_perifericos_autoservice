from django.urls import path

from .views import CardCredentialVerifyView, CardInspectView, CardReaderStatusView

urlpatterns = [
    path(
        "devices/card-reader/",
        CardReaderStatusView.as_view(),
        name="card-reader-status",
    ),
    path(
        "devices/card-reader/inspect/",
        CardInspectView.as_view(),
        name="card-reader-inspect",
    ),
    path(
        "devices/card-reader/credential/verify/",
        CardCredentialVerifyView.as_view(),
        name="card-reader-credential-verify",
    ),
]
