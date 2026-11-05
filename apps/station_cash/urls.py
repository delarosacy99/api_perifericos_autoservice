from django.urls import path
from . import views as v

urlpatterns = [
    path("attentions/start/", v.AttentionStartView.as_view()),
    path("attentions/<uuid:attention_id>/", v.AttentionStateView.as_view()),
    path("attentions/<uuid:attention_id>/heartbeat/", v.AttentionHeartbeatView.as_view()),
    path("attentions/<uuid:attention_id>/stop/", v.AttentionStopView.as_view()),
]
