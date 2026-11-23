"""Consumer entrypoint for ``huey_consumer config.worker.huey``.

Django has to be configured before ``config.huey`` (and therefore any task
module) is imported, so this module exists purely to own that ordering.

Import every task module below: Huey only knows about a task if the decorated
function was imported in the consumer process.
"""

from __future__ import annotations

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

import apps.deployments.tasks  # noqa: E402,F401  (registers tasks)
import apps.servers.tasks  # noqa: E402,F401
from config.huey import huey  # noqa: E402,F401
