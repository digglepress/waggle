"""Cross-process locks for operations that must be globally serialized.

A module-level ``threading.Semaphore`` only limits one gunicorn worker. These
locks live in the data directory, so every web and worker process shares them.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings


class LockBusy(RuntimeError):
    pass


@contextmanager
def operation_lock(name: str):
    path = Path(settings.LOCK_DIR) / f"{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LockBusy(name) from exc
            locked = True
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockBusy(name) from exc
            locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
