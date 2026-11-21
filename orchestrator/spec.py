"""Desired state: the specs we ask an orchestrator to make real.

These dataclasses are the contract between Waggle and any backend. They mirror
Docker Swarm's service model closely enough to be a near-lossless mapping,
without leaking SDK dictionaries into application code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PROTOCOLS = ("tcp", "udp")
PUBLISH_MODES = ("ingress", "host")
MOUNT_TYPES = ("volume", "bind", "tmpfs")
UPDATE_ORDERS = ("start-first", "stop-first")
FAILURE_ACTIONS = ("pause", "continue", "rollback")
RESTART_CONDITIONS = ("none", "on-failure", "any")


def _check(value: str, allowed: tuple[str, ...], field_name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {allowed}, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class PortMapping:
    """A port published by the swarm (usually only Traefik publishes ports)."""

    target: int
    published: int | None = None
    protocol: str = "tcp"
    mode: str = "ingress"

    def __post_init__(self) -> None:
        if not 1 <= self.target <= 65535:
            raise ValueError(f"target port out of range: {self.target}")
        if self.published is not None and not 1 <= self.published <= 65535:
            raise ValueError(f"published port out of range: {self.published}")
        _check(self.protocol, PROTOCOLS, "protocol")
        _check(self.mode, PUBLISH_MODES, "mode")


@dataclass(frozen=True, slots=True)
class Mount:
    """A filesystem mount inside the container."""

    target: str
    source: str = ""
    type: str = "volume"
    read_only: bool = False

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("mount target is required")
        _check(self.type, MOUNT_TYPES, "mount type")
        if self.type != "tmpfs" and not self.source:
            raise ValueError(f"{self.type} mount needs a source")


@dataclass(frozen=True, slots=True)
class Resources:
    """CPU and memory limits. ``None`` means "no limit"."""

    cpus: float | None = None
    memory_mb: int | None = None
    reserve_cpus: float | None = None
    reserve_memory_mb: int | None = None


@dataclass(frozen=True, slots=True)
class Healthcheck:
    """A shell healthcheck, the same one ``docker service create`` takes."""

    command: str
    interval_s: int = 15
    timeout_s: int = 5
    start_period_s: int = 10
    retries: int = 3


@dataclass(frozen=True, slots=True)
class UpdateConfig:
    """How Swarm rolls a service from one image to the next."""

    parallelism: int = 1
    order: str = "start-first"
    delay_s: int = 5
    monitor_s: int = 30
    failure_action: str = "rollback"
    max_failure_ratio: float = 0.0

    def __post_init__(self) -> None:
        _check(self.order, UPDATE_ORDERS, "update order")
        _check(self.failure_action, FAILURE_ACTIONS, "failure action")
        if self.parallelism < 1:
            raise ValueError("parallelism must be at least 1")


@dataclass(frozen=True, slots=True)
class RollbackConfig(UpdateConfig):
    """Rollback defaults differ: stop the old task before starting the new one."""

    order: str = "stop-first"
    failure_action: str = "pause"


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    condition: str = "on-failure"
    delay_s: int = 5
    max_attempts: int = 3
    window_s: int = 120

    def __post_init__(self) -> None:
        _check(self.condition, RESTART_CONDITIONS, "restart condition")


@dataclass(frozen=True, slots=True)
class Placement:
    """Where Swarm is allowed to run a task.

    ``constraints`` are plain Swarm expressions, for example
    ``node.labels.paas.node == edge-1``.
    """

    constraints: tuple[str, ...] = ()
    preferences: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "preferences", tuple(tuple(p) for p in self.preferences))


@dataclass(frozen=True, slots=True)
class FileRef:
    """A swarm secret or config mounted into the container as a file."""

    name: str
    target: str | None = None
    uid: str = "0"
    gid: str = "0"
    mode: int = 0o444


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """Everything needed to create or update a Swarm service."""

    name: str
    image: str
    replicas: int = 1
    global_mode: bool = False
    command: tuple[str, ...] | None = None
    args: tuple[str, ...] | None = None
    env: dict[str, str] = field(default_factory=dict)
    #: Service-level labels. Traefik's swarm provider reads these.
    labels: dict[str, str] = field(default_factory=dict)
    #: Labels on the container itself (not used for routing).
    container_labels: dict[str, str] = field(default_factory=dict)
    networks: tuple[str, ...] = ()
    ports: tuple[PortMapping, ...] = ()
    mounts: tuple[Mount, ...] = ()
    secrets: tuple[FileRef, ...] = ()
    configs: tuple[FileRef, ...] = ()
    resources: Resources = field(default_factory=Resources)
    healthcheck: Healthcheck | None = None
    update_config: UpdateConfig = field(default_factory=UpdateConfig)
    rollback_config: RollbackConfig | None = None
    restart_policy: RestartPolicy = field(default_factory=RestartPolicy)
    placement: Placement = field(default_factory=Placement)
    stop_grace_period_s: int = 10
    user: str | None = None
    working_dir: str | None = None
    hostname: str | None = None
    init: bool = False
    log_max_size: str = "10m"
    log_max_files: int = 3

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("service name is required")
        if not self.image:
            raise ValueError("image is required")
        if self.replicas < 0:
            raise ValueError("replicas cannot be negative")
        for attr in ("command", "args"):
            value = getattr(self, attr)
            if value is not None:
                object.__setattr__(self, attr, tuple(value))
        object.__setattr__(self, "networks", tuple(self.networks))
        for attr in ("ports", "mounts", "secrets", "configs"):
            object.__setattr__(self, attr, tuple(getattr(self, attr)))
