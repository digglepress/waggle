from __future__ import annotations

from typing import Any

from django.conf import settings


def waggle(request: Any) -> dict[str, Any]:
    return {
        "app_name": "Waggle",
        "app_version": settings.APP_VERSION,
        "app_tagline": "Modern Project & Cloud Swarm Management",
    }
