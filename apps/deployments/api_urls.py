from __future__ import annotations

from django.urls import path

from . import api

app_name = "api"

urlpatterns = [
    path("me", api.me, name="me"),
    path("services/", api.services, name="services"),
    path("services/<int:pk>/", api.service_detail, name="service"),
    path("services/<int:pk>/deploy", api.deploy, name="deploy"),
    path("services/<int:pk>/rollback", api.rollback, name="rollback"),
    path("deployments/<int:pk>/", api.deployment_detail, name="deployment"),
]
