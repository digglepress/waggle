"""Background jobs for servers.

The task body lives in ``services`` so it can be exercised without Huey.
"""

from __future__ import annotations

from config.huey import huey


@huey.task()
def bootstrap_server(server_id: int) -> None:
    from .models import Server
    from .services import run_bootstrap_for

    server = Server.objects.filter(pk=server_id).first()
    if server is None:
        return
    run_bootstrap_for(server)
