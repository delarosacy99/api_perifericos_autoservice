from django.urls import path

from . import views as v

urlpatterns = [
    path("sessions/", v.SessionView.as_view()),
    path("heartbeat/", v.HeartbeatView.as_view()),
    path("state/", v.StateView.as_view()),
    path("bootstrap/", v.BootstrapView.as_view()),
    path("catalog/", v.CatalogView.as_view()),
    path("recover/", v.RecoverView.as_view()),
    path("checkouts/", v.BeginView.as_view()),
    path("checkouts/<uuid:checkout_id>/", v.CheckoutView.as_view()),
    path("checkouts/<uuid:checkout_id>/cash/start/", v.CashStartView.as_view()),
    path("checkouts/<uuid:checkout_id>/seal/", v.SealView.as_view()),
    path("checkouts/<uuid:checkout_id>/finalize/", v.FinalizeView.as_view()),
    path("checkouts/<uuid:checkout_id>/tickets/<str:action>/", v.TicketView.as_view()),
    path("print-jobs/<uuid:job_id>/claim/", v.PrintClaimView.as_view()),
    path("print-jobs/<uuid:job_id>/report/", v.PrintReportView.as_view()),
    path("print-jobs/<uuid:job_id>/resolve/", v.PrintResolveView.as_view()),
]
