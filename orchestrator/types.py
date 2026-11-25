"""Observed state: what the orchestrator reports back.

Kept separate from :mod:`orchestrator.spec` so it is obvious which direction
data flows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SwarmInfo:
    """The local node's view of the cluster it belongs to."""

    node_id: str
    node_addr: str
    cluster_id: str
    control_available: bool
    local_state: str  # active | inactive | pending | error | locked
    hostname: str = ""

    @property
    def is_manager(self) -> bool:
        return self.control_available


@dataclass(frozen=True, slots=True)
class NodeInfo:
    id: str
    hostname: str
    role: str  # manager | worker
    availability: str  # active | pause | drain
    state: str  # ready | down | unknown | disconnected
    address: str
    architecture: str = ""
    os: str = ""
    cpu_cores: int = 0
    memory_mb: int = 0
    docker_version: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    is_leader: bool = False

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    id: str
    name: str
    image: str
    mode: str  # replicated | global
    replicas_desired: int
    replicas_running: int
    version: int = 0
    update_state: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    ports: tuple[str, ...] = ()
    created_at: datetime | None = None

    @property
    def is_converged(self) -> bool:
        return self.replicas_desired == self.replicas_running


@dataclass(frozen=True, slots=True)
class TaskInfo:
    id: str
    service_id: str
    node_id: str
    slot: int
    state: str  # new | pending | assigned | preparing | starting | running | failed | ...
    desired_state: str
    error: str = ""
    message: str = ""
    updated_at: datetime | None = None
    container_id: str = ""

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def is_failed(self) -> bool:
        return self.state in {"failed", "rejected", "orphaned"}

    @property
    def is_pulling(self) -> bool:
        """True while Swarm is still fetching the image or scheduling the task.

        Docker does not report pull byte counts for Swarm tasks, so these
        states are the only progress signal available.
        """
        return self.state in PULL_TASK_STATES


#: Task states that mean the rollout has not started the container yet.
#: ``preparing`` is where Swarm pulls the image; the others are scheduling.
PULL_TASK_STATES = frozenset({"new", "pending", "assigned", "preparing"})


@dataclass(frozen=True, slots=True)
class ConvergenceResult:
    converged: bool
    desired: int
    running: int
    failed: int
    elapsed_s: float
    tasks: tuple[TaskInfo, ...] = ()
    message: str = ""
    pulling: bool = False


@dataclass(frozen=True, slots=True)
class ConvergenceProgress:
    """A snapshot handed to ``on_progress`` while a rollout is waiting."""

    desired: int
    running: int
    failed: int
    elapsed_s: float
    pulling: bool
    message: str


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    id: str
    name: str
    driver: str
    scope: str
    attachable: bool
