"""The Huey task queue.

SQLite-backed, no Redis. The consumer is a separate process::

    huey_consumer.py config.worker.huey -w 2 -k thread

``config.worker`` calls ``django.setup()`` before importing this module, so
tasks can import models freely.
"""

from __future__ import annotations

from django.conf import settings
from huey import SqliteHuey

huey = SqliteHuey(
    name="waggle",
    filename=str(settings.DATA_DIR / "huey.sqlite3"),
    results=True,
    store_none=False,
    immediate=getattr(settings, "HUEY_IMMEDIATE", False),
    utc=True,
)
