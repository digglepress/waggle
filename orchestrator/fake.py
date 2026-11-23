"""In-memory orchestrator used by tests and by ``--fake`` demos.

It behaves like a tiny cluster: services converge instantly, tasks are reported
per node, and every call is recorded so tests can assert on intent.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

from .base import Orchestrator
from .errors import OrchestratorError, OrchestratorUnavailable, ServiceNotFound
from .spec import ServiceSpec
from .types import (
    NetworkInfo,
    NodeInfo,
    ServiceInfo,
    SwarmInfo,
    TaskInfo,
)


def _now() -> datetime:
    return datetime.now(UTC)


class FakeOrchestrator(Orchestrator):
    """A singleton so every ``get_orchestrator()`` call shares state."""

    _instance: FakeOrchestrator | None = None

    def __new__(cls, *args: object, **kwargs: object) -> FakeOrchestrator:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialised", False):
            return
        self._initialised = True
        self.reset_state()

    # ------------------------------------------------------------------ #
    # Test controls
    # ------------------------------------------------------------------ #

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton so the next call starts from an empty cluster."""
        cls._instance = None

    def reset_state(self) -> None:
        self._seq = itertools.count(1)
        self.networks: dict[str, NetworkInfo] = {}
        self.services: dict[str, dict] = {}
        self.nodes: dict[str, NodeInfo] = {}
        self.secrets: dict[str, bytes] = {}
        self.configs: dict[str, bytes] = {}
        self.volumes: set[str] = set()
        self.log_lines: dict[str, list[str]] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.unavailable = False
        self._swarm: SwarmInfo | None = None
        self.fail_create: Exception | None = None

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    def call_names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def add_node(
        self,
        hostname: str,
        *,
        role: str = "worker",
        state: str = "ready",
        availability: str = "active",
        address: str | None = None,
        labels: dict[str, str] | None = None,
        cpu_cores: int = 4,
        memory_mb: int = 4096,
        node_id: str | None = None,
    ) -> NodeInfo:
        node = NodeInfo(
            id=node_id or f"node_{next(self._seq)}",
            hostname=hostname,
            role=role,
            availability=availability,
            state=state,
            address=address or f"10.0.0.{next(self._seq)}",
            architecture="x86_64",
            os="linux",
            cpu_cores=cpu_cores,
            memory_mb=memory_mb,
            docker_version="27.5.1",
            labels=dict(labels or {}),
            is_leader=role == "manager" and state == "ready",
        )
        self.nodes[node.id] = node
        return node

    def set_swarm(self, *, control_available: bool = True) -> SwarmInfo:
        leader = next((n for n in self.nodes.values() if n.role == "manager"), None)
        self._swarm = SwarmInfo(
            node_id=leader.id if leader else "node_local",
            node_addr=leader.address if leader else "127.0.0.1",
            cluster_id="fake-cluster-id",
            control_available=control_available,
            local_state="active",
            hostname=leader.hostname if leader else "localhost",
        )
        return self._swarm

    def set_running(self, name_or_id: str, running: int) -> None:
        record = self._find(name_or_id)
        record["running"] = running

    def set_logs(self, name_or_id: str, lines: list[str]) -> None:
        record = self._find(name_or_id)
        self.log_lines[record["spec"].name] = lines

    # ------------------------------------------------------------------ #
    # Cluster
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        self._record("ping")
        if self.unavailable:
            raise OrchestratorUnavailable("fake orchestrator is unavailable")
        return True

    def swarm_info(self) -> SwarmInfo | None:
        self._record("swarm_info")
        return self._swarm

    # ------------------------------------------------------------------ #
    # Networks
    # ------------------------------------------------------------------ #

    def ensure_network(
        self, name: str, *, attachable: bool = True, encrypted: bool = False
    ) -> str:
        self._record("ensure_network", name, attachable=attachable, encrypted=encrypted)
        if name not in self.networks:
            self.networks[name] = NetworkInfo(
                id=f"net_{next(self._seq)}",
                name=name,
                driver="overlay",
                scope="swarm",
                attachable=attachable,
            )
        return self.networks[name].id

    def remove_network(self, name: str) -> None:
        self._record("remove_network", name)
        self.networks.pop(name, None)

    def list_networks(self) -> list[NetworkInfo]:
        self._record("list_networks")
        return sorted(self.networks.values(), key=lambda net: net.name)

    # ------------------------------------------------------------------ #
    # Services
    # ------------------------------------------------------------------ #

    def create_service(self, spec: ServiceSpec) -> str:
        self._record("create_service", spec)
        if self.fail_create is not None:
            raise self.fail_create
        if self._find_or_none(spec.name) is not None:
            raise OrchestratorError(f"service {spec.name} already exists")
        service_id = f"svc_{next(self._seq)}"
        self.services[service_id] = {
            "id": service_id,
            "spec": spec,
            "running": spec.replicas,
            "version": 1,
            "created_at": _now(),
        }
        return service_id

    def update_service(
        self, name_or_id: str, spec: ServiceSpec, *, force: bool = False
    ) -> None:
        self._record("update_service", name_or_id, spec, force=force)
        record = self._find(name_or_id)
        record["spec"] = spec
        record["running"] = spec.replicas
        record["version"] += 1

    def remove_service(self, name_or_id: str) -> None:
        self._record("remove_service", name_or_id)
        record = self._find(name_or_id)
        del self.services[record["id"]]

    def get_service(self, name_or_id: str) -> ServiceInfo | None:
        self._record("get_service", name_or_id)
        record = self._find_or_none(name_or_id)
        return self._to_info(record) if record else None

    def list_services(self, *, prefix: str | None = None) -> list[ServiceInfo]:
        self._record("list_services", prefix=prefix)
        infos = [self._to_info(record) for record in self.services.values()]
        if prefix:
            infos = [info for info in infos if info.name.startswith(prefix)]
        return sorted(infos, key=lambda info: info.name)

    def service_tasks(self, service_id: str) -> list[TaskInfo]:
        self._record("service_tasks", service_id)
        record = self._find(service_id)
        desired = self._desired(record)
        running = int(record["running"])
        ready_nodes = [node for node in self.nodes.values() if node.is_ready]
        tasks: list[TaskInfo] = []
        for slot in range(1, max(desired, running) + 1):
            node = ready_nodes[(slot - 1) % len(ready_nodes)] if ready_nodes else None
            state = "running" if slot <= running else "pending"
            tasks.append(
                TaskInfo(
                    id=f"task_{record['id']}_{slot}",
                    service_id=record["id"],
                    node_id=node.id if node else "node_unknown",
                    slot=slot,
                    state=state,
                    desired_state="running",
                    updated_at=_now(),
                )
            )
        return tasks

    def service_logs(
        self,
        name_or_id: str,
        *,
        tail: int = 200,
        since: int | None = None,
        follow: bool = False,
        timestamps: bool = False,
    ) -> Iterator[str]:
        self._record("service_logs", name_or_id, tail=tail, since=since, follow=follow,
                     timestamps=timestamps)
        record = self._find(name_or_id)
        lines = self.log_lines.get(record["spec"].name, [f"log line for {record['spec'].name}"])
        yield from lines[-tail:]

    def rollback_service(self, name_or_id: str) -> None:
        self._record("rollback_service", name_or_id)

    # ------------------------------------------------------------------ #
    # Secrets, configs, volumes
    # ------------------------------------------------------------------ #

    def create_secret(
        self, name: str, data: bytes, *, labels: dict[str, str] | None = None
    ) -> str:
        self._record("create_secret", name, data, labels=labels)
        self.secrets[name] = data
        return f"secret_{next(self._seq)}"

    def remove_secret(self, name: str) -> None:
        self._record("remove_secret", name)
        self.secrets.pop(name, None)

    def create_config(
        self, name: str, data: bytes, *, labels: dict[str, str] | None = None
    ) -> str:
        self._record("create_config", name, data, labels=labels)
        self.configs[name] = data
        return f"config_{next(self._seq)}"

    def remove_config(self, name: str) -> None:
        self._record("remove_config", name)
        self.configs.pop(name, None)

    def create_volume(self, name: str, *, labels: dict[str, str] | None = None) -> None:
        self._record("create_volume", name, labels=labels)
        self.volumes.add(name)

    def remove_volume(self, name: str) -> None:
        self._record("remove_volume", name)
        self.volumes.discard(name)

    # ------------------------------------------------------------------ #
    # Nodes
    # ------------------------------------------------------------------ #

    def list_nodes(self) -> list[NodeInfo]:
        self._record("list_nodes")
        return sorted(self.nodes.values(), key=lambda node: node.hostname)

    def update_node(
        self,
        node_id: str,
        *,
        availability: str | None = None,
        labels: dict[str, str] | None = None,
        role: str | None = None,
    ) -> None:
        self._record("update_node", node_id, availability=availability, labels=labels, role=role)
        if node_id not in self.nodes:
            raise OrchestratorError(f"no such node: {node_id}")
        node = self.nodes[node_id]
        changes: dict[str, object] = {}
        if availability is not None:
            changes["availability"] = availability
        if labels is not None:
            changes["labels"] = dict(labels)
        if role is not None:
            changes["role"] = role
        self.nodes[node_id] = replace(node, **changes)

    def remove_node(self, node_id: str, *, force: bool = False) -> None:
        self._record("remove_node", node_id, force=force)
        self.nodes.pop(node_id, None)

    def join_tokens(self) -> dict[str, str]:
        self._record("join_tokens")
        return {
            "Worker": "SWMTKN-1-fake-worker-token",
            "Manager": "SWMTKN-1-fake-manager-token",
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _find(self, name_or_id: str) -> dict:
        record = self._find_or_none(name_or_id)
        if record is None:
            raise ServiceNotFound(f"no such service: {name_or_id}")
        return record

    def _find_or_none(self, name_or_id: str) -> dict | None:
        if name_or_id in self.services:
            return self.services[name_or_id]
        for record in self.services.values():
            if record["spec"].name == name_or_id:
                return record
        return None

    def _desired(self, record: dict) -> int:
        spec: ServiceSpec = record["spec"]
        if spec.global_mode:
            return sum(1 for node in self.nodes.values() if node.is_ready)
        return spec.replicas

    def _to_info(self, record: dict) -> ServiceInfo:
        spec: ServiceSpec = record["spec"]
        return ServiceInfo(
            id=record.get("id", ""),
            name=spec.name,
            image=spec.image,
            mode="global" if spec.global_mode else "replicated",
            replicas_desired=self._desired(record),
            replicas_running=int(record["running"]),
            version=int(record["version"]),
            labels=dict(spec.labels),
            created_at=record["created_at"],
        )
