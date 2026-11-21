from __future__ import annotations

from django.apps import AppConfig


class ServersConfig(AppConfig):
    name = "apps.servers"
    verbose_name = "Servers"
    default_auto_field = "django.db.models.BigAutoField"
