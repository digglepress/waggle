"""The orchestrator interface.

Every backend (Swarm today, something else later) implements this. Application
code depends only on this module, which is what keeps the Swarm bet reversible.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator

from .errors import OrchestratorError, ServiceNotFound
from .spec import ServiceSpec
from .types import (
    ConvergenceResult,
    NetworkInfo,
    NodeInfo,
    ServiceInfo,
    SwarmInfo,
    TaskInfo,
)


class Orchestrator(ABC):
    """Desired-state API for a container cluster."""

    # ------------------------------------------------------------------ #
    # Cluster
    # ------------------------------------------------------------------ #

    @abstractmethod
    def ping(self) -> bool:
        """Return True if the backend is reachable, raise otherwise."""

    @abstractmethod
    def swarm_info(self) -> SwarmInfo | None:
        """Return cluster info for the local node, or None if not in a swarm."""

    # ------------------------------------------------------------------ #
    # Networks
    # ------------------------------------------------------------------ #

    @abstractmethod
    def ensure_network(
        self, name: str, *, attachable: bool = True, encrypted: bool = False
    ) -> str:
        """Create an overlay network if missing and return its id."""

    @abstractmethod
    def remove_network(self, name: str) -> None: ...

    @abstractmethod
    def list_networks(self) -> list[NetworkInfo]: ...

    # ------------------------------------------------------------------ #
    # Services
    # ------------------------------------------------------------------ #

    @abstractmethod
    def create_service(self, spec: ServiceSpec) -> str: ...

    @abstractmethod
    def update_service(self, name_or_id: str, spec: ServiceSpec, *, force: bool = False) -> None: ...

    @abstractmethod
    def remove_service(self, name_or_id: str) -> None: ...

    @abstractmethod
    def get_service(self, name_or_id: str) -> ServiceInfo | None: ...

    @abstractmethod
    def list_services(self, *, prefix: str | None = None) -> list[ServiceInfo]: ...

    @abstractmethod
    def service_tasks(self, service_id: str) -> list[TaskInfo]: ...

    @abstractmethod
    def service_logs(
        self,
        name_or_id: str,
        *,
        tail: int = 200,
        since: int | None = None,
        follow: bool = False,
        timestamps: bool = False,
    ) -> Iterator[str]:
        """Stream decoded log lines. ``follow=True`` blocks until the caller stops."""

    @abstractmethod
    def rollback_service(self, name_or_id: str) -> None: ...

    def previous_service_labels(self, name_or_id: str) -> dict[str, str]:
        """Labels restored by a native rollback, if the backend exposes them."""
        return {}

    # ------------------------------------------------------------------ #
    # Secrets, configs, volumes
    # ------------------------------------------------------------------ #

    @abstractmethod
    def create_secret(self, name: str, data: bytes, *, labels: dict[str, str] | None = None) -> str: ...

    @abstractmethod
    def remove_secret(self, name: str) -> None: ...

    @abstractmethod
    def create_config(self, name: str, data: bytes, *, labels: dict[str, str] | None = None) -> str: ...

    @abstractmethod
    def remove_config(self, name: str) -> None: ...

    @abstractmethod
    def create_volume(self, name: str, *, labels: dict[str, str] | None = None) -> None: ...

    @abstractmethod
    def remove_volume(self, name: str) -> None: ...

    # ------------------------------------------------------------------ #
    # Nodes
    # ------------------------------------------------------------------ #

    @abstractmethod
    def list_nodes(self) -> list[NodeInfo]: ...

    @abstractmethod
    def update_node(
        self,
        node_id: str,
        *,
        availability: str | None = None,
        labels: dict[str, str] | None = None,
        role: str | None = None,
    ) -> None: ...

    @abstractmethod
    def remove_node(self, node_id: str, *, force: bool = False) -> None: ...

    @abstractmethod
    def join_tokens(self) -> dict[str, str]:
        """Return ``{"Worker": ..., "Manager": ...}`` join tokens."""

    # ------------------------------------------------------------------ #
    # Shared behaviour
    # ------------------------------------------------------------------ #

    def wait_for_convergence(
        self,
        service_id: str,
        *,
        replicas: int | None = None,
        timeout: float = 180.0,
        poll_interval: float = 1.0,
    ) -> ConvergenceResult:
        """Poll until the service has the requested number of running tasks.

        Returns a result instead of raising so callers can decide whether a
        partial rollout is a failure. ``ConvergenceError`` is reserved for
        "the service vanished", which is always a bug.
        """
        started = time.monotonic()
        while True:
            service = self.get_service(service_id)
            if service is None:
                raise ServiceNotFound(f"service {service_id} disappeared during rollout")
            tasks = self.service_tasks(service_id)
            running = sum(1 for task in tasks if task.is_running)
            failed = sum(1 for task in tasks if task.is_failed)
            desired = replicas if replicas is not None else service.replicas_desired
            elapsed = time.monotonic() - started

            if desired == 0 or running >= desired:
                return ConvergenceResult(
                    converged=True,
                    desired=desired,
                    running=running,
                    failed=failed,
                    elapsed_s=round(elapsed, 2),
                    tasks=tuple(tasks),
                )
            if running == 0 and failed >= max(desired, 1):
                return ConvergenceResult(
                    converged=False,
                    desired=desired,
                    running=running,
                    failed=failed,
                    elapsed_s=round(elapsed, 2),
                    tasks=tuple(tasks),
                    message=_first_error(tasks) or "all tasks failed",
                )
            if elapsed >= timeout:
                return ConvergenceResult(
                    converged=False,
                    desired=desired,
                    running=running,
                    failed=failed,
                    elapsed_s=round(elapsed, 2),
                    tasks=tuple(tasks),
                    message=_first_error(tasks)
                    or f"{running}/{desired} tasks running after {timeout:.0f}s",
                )
            time.sleep(poll_interval)

    def close(self) -> None:  # noqa: B027 - optional hook, not every backend holds resources
        """Release backend resources. Safe to call more than once."""

    def __enter__(self) -> Orchestrator:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _first_error(tasks: list[TaskInfo]) -> str:
    for task in tasks:
        if task.error:
            return task.error
        if task.message:
            return task.message
    return ""


def require(condition: bool, message: str) -> None:
    """Small helper for backend preconditions."""
    if not condition:
        raise OrchestratorError(message)
