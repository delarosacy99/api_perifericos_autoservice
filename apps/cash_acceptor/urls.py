from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import CashAcceptorStatusView, CashCollectionViewSet, SimulateNoteView

router = SimpleRouter()
router.register("cash/collections", CashCollectionViewSet, basename="cash-collection")

urlpatterns = [
    path("", include(router.urls)),
    path("devices/cash-acceptor/", CashAcceptorStatusView.as_view(), name="cash-acceptor-status"),
    path(
        "devices/cash-acceptor/simulate-note/",
        SimulateNoteView.as_view(),
        name="cash-acceptor-simulate-note",
    ),
]
