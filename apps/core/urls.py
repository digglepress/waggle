from __future__ import annotations

from django.urls import path

from . import team, views

app_name = "core"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("cluster/", views.ClusterView.as_view(), name="cluster"),
    path("healthz", views.health, name="health"),
    path("readyz", views.ready, name="ready"),
    path("metrics", views.metrics, name="metrics"),
    path("audit/", views.audit_log, name="audit"),
    path("team/", team.team, name="team"),
    path("team/<int:pk>/", team.update_team, name="team_member"),
]
