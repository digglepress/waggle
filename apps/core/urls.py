from __future__ import annotations

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("cluster/", views.ClusterView.as_view(), name="cluster"),
    path("healthz", views.health, name="health"),
]
