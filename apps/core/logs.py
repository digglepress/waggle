"""Append-only log files for long-running tasks.

Build logs, bootstrap logs and deploy logs all follow the same pattern: a
worker appends lines, the web process tails the file over SSE. No Redis, no
message bus, and the log survives a restart.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def server_log_path(server_id: int) -> Path:
    return settings.LOG_DIR / f"server-{server_id}.log"


def deployment_log_path(deployment_id: int) -> Path:
    return settings.BUILD_LOG_DIR / f"deployment-{deployment_id}.log"


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")


def reset_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def read_new_lines(path: Path, position: int) -> tuple[list[str], int]:
    """Return complete lines appended since ``position`` and the new position.

    A trailing partial line is left for the next poll, so the caller never
    renders half a log line.
    """
    if not path.exists():
        return [], position
    if path.stat().st_size < position:  # truncated or replaced
        position = 0
    with path.open("rb") as handle:
        handle.seek(position)
        data = handle.read()
    if not data:
        return [], position
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut == -1:
            return [], position
        data = data[: cut + 1]
    return data.decode("utf-8", "replace").splitlines(), position + len(data)
