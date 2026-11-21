from __future__ import annotations

from typing import Any


def waggle(request: Any) -> dict[str, Any]:
    return {
        "app_name": "Waggle",
        "app_version": "0.1.0",
        "app_tagline": "Swarm-native deployments",
    }
