"""Best-effort outbound notifications for deployment events.

Waggle has no Redis or Celery, so delivery is a single short HTTP POST from the
Huey worker. A failure is logged and never changes the deployment result.
"""

from __future__ import annotations

import json
import logging
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger("waggle.notify")


def _post(url: str, payload: dict) -> None:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "waggle-notify"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310 - operator-configured HTTPS URL
        response.read(1024)


def deployment_finished(job) -> None:
    """Send a Slack-compatible JSON message when configured."""
    url = getattr(settings, "NOTIFY_WEBHOOK_URL", "")
    if not url:
        return
    state = job.state
    if state not in {"failed", "submitted"}:
        return
    app = job.application
    heading = f"Waggle deployment {'failed' if state == 'failed' else 'deployed'}: {app.slug}"
    lines = [heading, f"Project: {app.scope.project.name} / {app.scope.name}"]
    if job.image:
        lines.append(f"Image: {job.image}")
    if job.error:
        lines.append(job.error[:500])
    payload = {"text": "\n".join(lines)}
    try:
        _post(url, payload)
    except Exception:  # noqa: BLE001 - notifications must not break deployments
        logger.exception("Could not deliver the deployment notification")
