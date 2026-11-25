"""The orchestrator interface.

Every backend (Swarm today, something else later) implements this. Application
code depends only on this module, which is what keeps the Swarm bet reversible.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Iterator

from .errors import OrchestratorError, ServiceNotFound
from .spec import ServiceSpec
from .types import (
    ConvergenceProgress,
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
        pull_timeout: float | None = None,
        poll_interval: float = 1.0,
        progress_interval: float = 10.0,
        on_progress: Callable[[ConvergenceProgress], None] | None = None,
    ) -> ConvergenceResult:
        """Poll until the service has the requested number of running tasks.

        A slow image pull must not look like a failed rollout. While tasks are
        still ``preparing`` (Docker's pull phase) the deadline is extended to
        ``pull_timeout``; once the pull finishes the normal ``timeout`` applies
        again. ``on_progress`` receives snapshots on task-state changes and
        periodically when the state is unchanged.

        Returns a result instead of raising so callers can decide whether a
        partial rollout is a failure. ``ConvergenceError`` is reserved for
        "the service vanished", which is always a bug.
        """
        started = time.monotonic()
        pull_timeout = max(timeout, pull_timeout or timeout)
        hard_deadline = started + pull_timeout
        deadline = started + timeout
        node_names = self._node_names()
        last_signature: tuple[tuple[str, str], ...] | None = None
        next_progress = started + min(progress_interval, 5.0)
        extended = False
        was_pulling = False

        while True:
            service = self.get_service(service_id)
            if service is None:
                raise ServiceNotFound(f"service {service_id} disappeared during rollout")
            tasks = [
                task for task in self.service_tasks(service_id)
                if task.desired_state != "shutdown"
            ]
            running = sum(1 for task in tasks if task.is_running)
            failed = sum(1 for task in tasks if task.is_failed)
            desired = replicas if replicas is not None else service.replicas_desired
            elapsed = time.monotonic() - started
            pulling = running < desired and any(task.is_pulling for task in tasks)
            signature = tuple(sorted((task.state, task.message) for task in tasks))

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
                    pulling=pulling,
                )

            now = time.monotonic()
            changed = last_signature is not None and signature != last_signature
            if on_progress and (changed or now >= next_progress):
                on_progress(ConvergenceProgress(
                    desired=desired,
                    running=running,
                    failed=failed,
                    elapsed_s=round(elapsed, 2),
                    pulling=pulling,
                    message=_progress_message(
                        tasks, desired, running, elapsed, node_names,
                    ),
                ))
                next_progress = now + progress_interval
            last_signature = signature

            if was_pulling and not pulling:
                # The image is available now. Start the normal startup window
                # from here instead of counting the pull against it.
                deadline = now + timeout
                extended = False
                if on_progress:
                    on_progress(ConvergenceProgress(
                        desired=desired,
                        running=running,
                        failed=failed,
                        elapsed_s=round(elapsed, 2),
                        pulling=False,
                        message="Image pull finished; waiting for the container to start.",
                    ))
                    next_progress = now + progress_interval

            if pulling:
                if now >= hard_deadline:
                    return ConvergenceResult(
                        converged=False,
                        desired=desired,
                        running=running,
                        failed=failed,
                        elapsed_s=round(elapsed, 2),
                        tasks=tuple(tasks),
                        message=_failure_message(tasks, desired, running, elapsed, pulling),
                        pulling=True,
                    )
                if now >= deadline and not extended:
                    extended = True
                    if on_progress:
                        on_progress(ConvergenceProgress(
                            desired=desired,
                            running=running,
                            failed=failed,
                            elapsed_s=round(elapsed, 2),
                            pulling=True,
                            message=(
                                "Image pull is still in progress after "
                                f"{timeout:.0f}s; waiting up to "
                                f"{pull_timeout:.0f}s for the rollout."
                            ),
                        ))
                        next_progress = now + progress_interval
            elif now >= deadline:
                return ConvergenceResult(
                    converged=False,
                    desired=desired,
                    running=running,
                    failed=failed,
                    elapsed_s=round(elapsed, 2),
                    tasks=tuple(tasks),
                    message=_failure_message(tasks, desired, running, elapsed, pulling),
                    pulling=False,
                )
            was_pulling = pulling
            time.sleep(poll_interval)

    def _node_names(self) -> dict[str, str]:
        """Best-effort node id to hostname map for progress messages."""
        try:
            return {node.id: node.hostname for node in self.list_nodes() if node.hostname}
        except Exception:  # noqa: BLE001 - progress text must never break a rollout
            return {}

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


def _progress_message(
    tasks: list[TaskInfo],
    desired: int,
    running: int,
    elapsed: float,
    node_names: dict[str, str],
) -> str:
    counts = Counter(task.state for task in tasks)
    detail = ", ".join(f"{count} {state}" for state, count in sorted(counts.items()))
    pending_nodes = sorted({
        node_names.get(task.node_id) or task.node_id[:12] or "unknown"
        for task in tasks
        if not task.is_running and not task.is_failed
    })
    where = f" on {', '.join(pending_nodes)}" if pending_nodes else ""
    lead = (
        "Waiting for image pull and task startup"
        if any(task.is_pulling for task in tasks)
        else "Waiting for task startup"
    )
    return (
        f"{lead}: {running}/{desired} running"
        f" ({detail or 'no tasks yet'}){where}, {elapsed:.0f}s elapsed."
    )


def _failure_message(
    tasks: list[TaskInfo],
    desired: int,
    running: int,
    elapsed: float,
    pulling: bool,
) -> str:
    error = next((task.error for task in tasks if task.error), "")
    if error:
        return error
    if pulling:
        return (
            f"image pull still in progress after {elapsed:.0f}s "
            f"({running}/{desired} running)"
        )
    failed_message = next(
        (task.message for task in tasks if task.is_failed and task.message), "",
    )
    if failed_message:
        return failed_message
    return f"{running}/{desired} tasks running after {elapsed:.0f}s"


def require(condition: bool, message: str) -> None:
    """Small helper for backend preconditions."""
    if not condition:
        raise OrchestratorError(message)
