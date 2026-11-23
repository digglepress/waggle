"""Write audit events without letting an audit failure break the request."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from .models import AuditEvent

logger = logging.getLogger("waggle.audit")


def _ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address(value or ""))
    except ValueError:
        return None


def record(
    request: Any,
    action: str,
    *,
    target: Any = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent | None:
    """Record one action. Never include passwords, tokens, or env values."""

    user = getattr(request, "user", None)
    actor = user if getattr(user, "is_authenticated", False) else None
    target_type = target_id = target_label = ""
    if target is not None:
        meta = getattr(target, "_meta", None)
        target_type = getattr(meta, "label_lower", target.__class__.__name__.lower())
        target_id = str(getattr(target, "pk", ""))[:64]
        target_label = str(target)[:200]
    try:
        return AuditEvent.objects.create(
            actor=actor,
            action=action[:80],
            target_type=target_type[:80],
            target_id=target_id,
            target_label=target_label,
            detail=detail or {},
            request_id=str(getattr(request, "request_id", ""))[:64],
            ip=_ip(request.META.get("REMOTE_ADDR")),
            user_agent=str(request.META.get("HTTP_USER_AGENT", ""))[:300],
        )
    except Exception:  # noqa: BLE001 - auditing must not take down the action
        logger.exception("Could not write audit event %s", action)
        return None
