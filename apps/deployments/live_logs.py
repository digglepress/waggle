import json
import time
from collections import deque
from itertools import islice

from django.http import StreamingHttpResponse

from apps.core.logs import deployment_log_path
from apps.deployments.models import ACTIVE_DEPLOYMENT_STATES, Deployment
from orchestrator.errors import OrchestratorError

WINDOW_SECONDS = 25


def event(data, *, kind=None, event_id=None):
    return (
        (f"event: {kind}\n" if kind else "")
        + (f"id: {event_id}\n" if event_id is not None else "")
        + f"data: {json.dumps(data)}\n\n"
    )


def response(events):
    result = StreamingHttpResponse(events, content_type="text/event-stream")
    result["Cache-Control"] = "no-store"
    result["X-Accel-Buffering"] = "no"
    return result


def deployment_events(job_id, last_id=""):
    path = deployment_log_path(job_id)
    position = int(last_id) if last_id.isascii() and last_id.isdecimal() and len(last_id) < 19 else None
    deadline = time.monotonic() + WINDOW_SECONDS
    while time.monotonic() < deadline:
        partial_size = 0
        try:
            with path.open("rb") as file:
                if position is None:
                    start = max(0, file.seek(0, 2) - 65536)
                    file.seek(start)
                    if start:
                        file.readline()
                else:
                    file.seek(position)
                position = file.tell()
                while line := file.readline():
                    if not line.endswith(b"\n"):
                        partial_size = len(line)
                        break
                    position = file.tell()
                    yield event(line.decode("utf-8", "replace").rstrip("\r\n"), event_id=position)
        except FileNotFoundError:
            pass
        state = Deployment.objects.filter(pk=job_id).values_list("state", flat=True).first()
        if state not in {*ACTIVE_DEPLOYMENT_STATES}:
            # Re-read if the worker wrote the final line while the state query ran.
            try:
                if path.stat().st_size > (position or 0) + partial_size:
                    continue
            except FileNotFoundError:
                pass
            yield event(Deployment.State(state).label if state else "Not found", kind="done")
            return
        yield ": heartbeat\n\n"
        time.sleep(0.75)


def runtime_tail(orchestrator, service_id):
    logs = orchestrator.service_logs(service_id, tail=200, timestamps=True)
    try:
        return [line[:8192] for line in islice(logs, 400)]
    finally:
        close = getattr(logs, "close", None)
        if close:
            close()


def runtime_events(orchestrator, service_id):
    deadline = time.monotonic() + WINDOW_SECONDS
    recent = deque(maxlen=1000)
    while time.monotonic() < deadline:
        try:
            for line in runtime_tail(orchestrator, service_id):
                if line not in recent:
                    recent.append(line)
                    yield event(line)
        except OrchestratorError:
            yield event("Runtime logs are unavailable. Reopen this page to retry.", kind="done")
            return
        yield ": heartbeat\n\n"
        time.sleep(2)
