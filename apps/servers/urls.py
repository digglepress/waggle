from __future__ import annotations

from django.urls import path

from . import views

app_name = "servers"

urlpatterns = [
    path("servers/", views.ServerListView.as_view(), name="list"),
    path("servers/add/", views.ServerCreateView.as_view(), name="add"),
    path("servers/<int:pk>/", views.ServerDetailView.as_view(), name="detail"),
    path("servers/<int:pk>/action/", views.server_action, name="action"),
    path("servers/<int:pk>/log/", views.server_log, name="log"),
    path("keys/", views.SSHKeyListView.as_view(), name="keys"),
    path("keys/add/", views.SSHKeyCreateView.as_view(), name="key_add"),
]
