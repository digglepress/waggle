"""Open the ports Swarm needs, without locking the operator out.

We only add rules to a firewall that is already active. Enabling ``ufw`` on a
machine where SSH is not allowed would be a great way to lose access, so that
is explicitly a non-goal.
"""

from __future__ import annotations

from collections.abc import Callable

from .executor import Executor
from .preflight import SWARM_PORTS


def ensure_firewall_ports(
    ex: Executor,
    log: Callable[[str], None],
    *,
    ports: tuple[str, ...] = SWARM_PORTS,
) -> str:
    """Return which firewall was configured: ``ufw``, ``firewalld`` or ``none``."""

    if ex.run("command -v ufw", check=False).ok:
        status = ex.run("ufw status", check=False).stdout
        if "Status: active" in status:
            for port in ports:
                ex.run(f"ufw allow {port}", check=False)
            log(f"opened swarm ports in ufw ({', '.join(ports)})")
            return "ufw"
        log("ufw is installed but inactive; no host firewall rules were changed")
        return "none"

    if ex.run("command -v firewall-cmd", check=False).ok:
        active = ex.run("firewall-cmd --state", check=False).stdout.strip()
        if active == "running":
            for port in ports:
                ex.run(f"firewall-cmd --permanent --add-port={port}", check=False)
            ex.run("firewall-cmd --reload", check=False)
            log(f"opened swarm ports in firewalld ({', '.join(ports)})")
            return "firewalld"
        log("firewalld is installed but not running; no host firewall rules were changed")
        return "none"

    log(
        "no host firewall detected; make sure your cloud security group allows "
        + ", ".join(ports)
    )
    return "none"
