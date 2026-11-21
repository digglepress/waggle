"""Swarm membership commands, run over SSH on the target node."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass

from .executor import Executor, RemoteCommandError

MANAGER_PORT = 2377


@dataclass(frozen=True, slots=True)
class SwarmState:
    state: str = "inactive"  # inactive | active | pending | error | locked
    cluster_id: str = ""
    node_id: str = ""
    control_available: bool = False

    @property
    def is_active(self) -> bool:
        return self.state == "active"


def read_state(ex: Executor) -> SwarmState:
    fmt = (
        "{{.Swarm.LocalNodeState}}|{{.Swarm.Cluster.ID}}|"
        "{{.Swarm.NodeID}}|{{.Swarm.ControlAvailable}}"
    )
    result = ex.run(f"docker info --format '{fmt}'", check=False, timeout=30)
    if not result.ok:
        return SwarmState()

    parts = [part.strip() for part in result.stdout.strip().split("|")]
    while len(parts) < 4:
        parts.append("")
    state = "" if parts[0] == "<no value>" else parts[0]
    return SwarmState(
        state=state or "inactive",
        cluster_id="" if parts[1] == "<no value>" else parts[1],
        node_id="" if parts[2] == "<no value>" else parts[2],
        control_available=parts[3].lower() == "true",
    )


def init(
    ex: Executor,
    *,
    advertise_addr: str,
    log: Callable[[str], None] | None = None,
) -> None:
    command = [
        "docker",
        "swarm",
        "init",
        "--advertise-addr",
        advertise_addr,
    ]
    ex.run(_join(command), timeout=180, on_line=log)


def join(
    ex: Executor,
    *,
    manager_addr: str,
    token: str,
    advertise_addr: str | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    if not token:
        raise RemoteCommandError(
            ex.run("false  # empty swarm join token", check=False)
        )
    command = ["docker", "swarm", "join", "--token", token]
    if advertise_addr:
        command += ["--advertise-addr", advertise_addr]
    command += ["--listen-addr", "0.0.0.0:2377", manager_addr]
    ex.run(_join(command), timeout=300, on_line=log)


def leave(ex: Executor, *, force: bool = True) -> None:
    command = ["docker", "swarm", "leave"]
    if force:
        command.append("--force")
    ex.run(_join(command), check=False, timeout=120)


def _join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)
