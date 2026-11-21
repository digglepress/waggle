"""Access to the configured orchestrator backend."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from importlib import import_module

from django.conf import settings

from orchestrator.base import Orchestrator
from orchestrator.errors import OrchestratorError
from orchestrator.types import NodeInfo, ServiceInfo, SwarmInfo

_cache: dict[str, Orchestrator] = {}


def _load_backend(path: str):
    """Import ``module:attr`` or the Django-style ``module.attr``."""
    if ":" in path:
        module_path, attribute = path.split(":", 1)
    else:
        module_path, attribute = path.rsplit(".", 1)
    module = import_module(module_path)
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ImportError(f"{path} does not name a class in {module_path}") from exc


def get_orchestrator() -> Orchestrator:
    """Return the process-wide orchestrator for the current settings.

    The cache is keyed by the backend path, so ``override_settings`` in tests
    gets a fresh instance instead of a stale connection.
    """
    path = settings.ORCHESTRATOR
    orchestrator = _cache.get(path)
    if orchestrator is None:
        backend = _load_backend(path)
        kwargs: dict[str, object] = {}
        parameters = inspect.signature(backend).parameters
        if settings.DOCKER_URL and "base_url" in parameters:
            kwargs["base_url"] = settings.DOCKER_URL
        orchestrator = backend(**kwargs)
        _cache[path] = orchestrator
    return orchestrator


def reset_orchestrator_cache() -> None:
    for orchestrator in _cache.values():
        try:
            orchestrator.close()
        except Exception:  # noqa: BLE001 - closing must never mask the real error
            pass
    _cache.clear()


@dataclass(frozen=True, slots=True)
class ClusterStatus:
    ok: bool
    message: str = ""
    swarm: SwarmInfo | None = None
    nodes: list[NodeInfo] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)

    @property
    def managers(self) -> list[NodeInfo]:
        return [node for node in self.nodes if node.is_manager]

    @property
    def ready_nodes(self) -> list[NodeInfo]:
        return [node for node in self.nodes if node.is_ready]


def cluster_status(*, include_services: bool = False) -> ClusterStatus:
    """Never raises: the UI must render even when Docker is unreachable."""
    try:
        orchestrator = get_orchestrator()
        orchestrator.ping()
        swarm = orchestrator.swarm_info()
        nodes = orchestrator.list_nodes()
        services = orchestrator.list_services() if include_services else []
    except OrchestratorError as exc:
        return ClusterStatus(ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - a broken backend must not 500 the dashboard
        return ClusterStatus(ok=False, message=f"unexpected orchestrator error: {exc}")

    if swarm is None:
        return ClusterStatus(
            ok=False,
            message="this host is not part of a swarm yet — run deploy/install.sh",
            nodes=nodes,
        )
    return ClusterStatus(ok=True, swarm=swarm, nodes=nodes, services=services)
