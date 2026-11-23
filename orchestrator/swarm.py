"""Docker Swarm implementation of the orchestrator interface.

Everything that knows about ``docker-py`` lives here. The only SDK quirks that
leak out are the ones we deliberately normalise (see ``rollback_service``).

A note on why the low-level API is used for services: ``docker-py``'s
high-level ``services.create`` silently drops fields it does not know about,
and we need full control over the task template. The low-level calls are thin
wrappers over ``POST /services/create`` and ``POST /services/{id}/update``.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager

import docker
import requests
from docker.errors import APIError, DockerException, NotFound

from .base import Orchestrator
from .convert import create_kwargs, update_kwargs
from .errors import OrchestratorError, OrchestratorUnavailable, ServiceNotFound
from .spec import ServiceSpec
from .types import (
    NetworkInfo,
    NodeInfo,
    ServiceInfo,
    SwarmInfo,
    TaskInfo,
)

_MANAGED_LABEL = {"waggle.managed": "true"}


def _explain(exc: Exception) -> str:
    explanation = getattr(exc, "explanation", None)
    if isinstance(explanation, bytes):
        explanation = explanation.decode("utf-8", "replace")
    return (explanation or str(exc)).strip()


def _wrap(action: str):
    """Translate docker-py exceptions into orchestrator exceptions."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (OrchestratorError, ServiceNotFound):
                raise
            except NotFound as exc:
                raise ServiceNotFound(f"{action}: {_explain(exc)}") from exc
            except APIError as exc:
                raise OrchestratorError(f"{action}: {_explain(exc)}") from exc
            except (DockerException, requests.exceptions.RequestException) as exc:
                raise OrchestratorUnavailable(f"{action}: {_explain(exc)}") from exc

        return wrapper

    return decorator


class SwarmOrchestrator(Orchestrator):
    def __init__(self, base_url: str | None = None, timeout: int = 30) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._client: docker.DockerClient | None = None

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                if self._base_url:
                    self._client = docker.DockerClient(
                        base_url=self._base_url, timeout=self._timeout
                    )
                else:
                    self._client = docker.from_env(timeout=self._timeout)
            except (DockerException, requests.exceptions.RequestException) as exc:
                raise OrchestratorUnavailable(
                    f"cannot connect to the Docker daemon: {_explain(exc)}"
                ) from exc
        return self._client

    @property
    def api(self):
        return self.client.api

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    # ------------------------------------------------------------------ #
    # Cluster
    # ------------------------------------------------------------------ #

    @_wrap("ping docker")
    def ping(self) -> bool:
        return bool(self.client.ping())

    @_wrap("read swarm info")
    def swarm_info(self) -> SwarmInfo | None:
        info = self.client.info()
        swarm = info.get("Swarm") or {}
        state = swarm.get("LocalNodeState") or "inactive"
        if state in {"inactive", ""}:
            return None
        cluster = swarm.get("Cluster") or {}
        return SwarmInfo(
            node_id=swarm.get("NodeID", ""),
            node_addr=swarm.get("NodeAddr", ""),
            cluster_id=cluster.get("ID", ""),
            control_available=bool(swarm.get("ControlAvailable")),
            local_state=state,
            hostname=info.get("Name", ""),
        )

    # ------------------------------------------------------------------ #
    # Networks
    # ------------------------------------------------------------------ #

    @_wrap("ensure network")
    def ensure_network(
        self, name: str, *, attachable: bool = True, encrypted: bool = False
    ) -> str:
        existing = self.client.networks.list(names=[name])
        if existing:
            return existing[0].id
        options = {"encrypted": ""} if encrypted else None
        network = self.client.networks.create(
            name,
            driver="overlay",
            attachable=attachable,
            options=options,
            labels=dict(_MANAGED_LABEL),
        )
        return network.id

    @_wrap("remove network")
    def remove_network(self, name: str) -> None:
        for network in self.client.networks.list(names=[name]):
            network.remove()

    @_wrap("list networks")
    def list_networks(self) -> list[NetworkInfo]:
        networks = []
        for network in self.client.networks.list():
            attrs = network.attrs
            networks.append(
                NetworkInfo(
                    id=attrs.get("Id", ""),
                    name=attrs.get("Name", ""),
                    driver=attrs.get("Driver", ""),
                    scope=attrs.get("Scope", ""),
                    attachable=bool(attrs.get("Attachable", False)),
                )
            )
        return sorted(networks, key=lambda net: net.name)

    # ------------------------------------------------------------------ #
    # Services
    # ------------------------------------------------------------------ #

    @_wrap("create service")
    def create_service(self, spec: ServiceSpec) -> str:
        payload = create_kwargs(spec)
        payload["task_template"] = self._with_network_ids(payload["task_template"])
        self._resolve_file_refs(payload["task_template"])
        with self._service_api(spec) as api:
            response = api.create_service(**payload)
        return response["ID"]

    @_wrap("update service")
    def update_service(
        self, name_or_id: str, spec: ServiceSpec, *, force: bool = False
    ) -> None:
        service = self.client.services.get(name_or_id)
        payload = update_kwargs(spec)
        # The SDK sends a null Name unless it is supplied or fetched. Swarm
        # treats that as a rename; keep the existing name explicitly.
        payload["name"] = service.attrs["Spec"]["Name"]
        payload["task_template"] = self._with_network_ids(payload["task_template"])
        self._resolve_file_refs(payload["task_template"])
        if force:
            payload["task_template"]["ForceUpdate"] = (
                int(service.attrs.get("Version", {}).get("Index", 0)) + 1
            )
        with self._service_api(spec) as api:
            api.update_service(service.id, version=service.attrs["Version"]["Index"], **payload)

    @contextmanager
    def _service_api(self, spec: ServiceSpec):
        auth = spec.registry_auth
        if not auth:
            yield self.api
            return
        host, _ = docker.auth.resolve_repository_name(spec.image)
        canonical = "docker.io" if host == "registry-1.docker.io" else host
        if auth.host != canonical:
            raise OrchestratorError("Selected credentials do not match the image registry.")
        # Use a dedicated client so concurrent jobs never share credential state.
        with SwarmOrchestrator(self._base_url, self._timeout) as isolated:
            api = isolated.api
            api._auth_configs = docker.auth.AuthConfig({"auths": {host: {
                "username": auth.username,
                "password": auth.password,
                "serveraddress": docker.auth.INDEX_URL if host == "docker.io" else host,
            }}})
            yield api

    @_wrap("remove service")
    def remove_service(self, name_or_id: str) -> None:
        self.client.services.get(name_or_id).remove()

    @_wrap("get service")
    def get_service(self, name_or_id: str) -> ServiceInfo | None:
        try:
            service = self.client.services.get(name_or_id)
        except NotFound:
            return None
        matches = self.client.services.list(filters={"id": service.id}, status=True)
        return self._service_info(matches[0].attrs) if matches else None

    @_wrap("list services")
    def list_services(self, *, prefix: str | None = None) -> list[ServiceInfo]:
        filters = {"label": "waggle.managed=true"} if prefix is None else None
        services = self.client.services.list(filters=filters, status=True)
        infos = [self._service_info(service.attrs) for service in services]
        if prefix:
            infos = [info for info in infos if info.name.startswith(prefix)]
        return sorted(infos, key=lambda info: info.name)

    @_wrap("list tasks")
    def service_tasks(self, service_id: str) -> list[TaskInfo]:
        raw_tasks = self.api.tasks(filters={"service": service_id})
        tasks = []
        for task in raw_tasks:
            status = task.get("Status") or {}
            tasks.append(
                TaskInfo(
                    id=task.get("ID", ""),
                    service_id=task.get("ServiceID", ""),
                    node_id=task.get("NodeID", ""),
                    slot=int(task.get("Slot", 0)),
                    state=status.get("State", "unknown"),
                    desired_state=task.get("DesiredState", ""),
                    error=status.get("Err", "") or "",
                    message=status.get("Message", "") or "",
                    container_id=(status.get("ContainerStatus") or {}).get("ContainerID", ""),
                )
            )
        return tasks

    @_wrap("read service logs")
    def service_logs(
        self,
        name_or_id: str,
        *,
        tail: int = 200,
        since: int | None = None,
        follow: bool = False,
        timestamps: bool = False,
    ) -> Iterator[str]:
        service = self.client.services.get(name_or_id)
        stream = service.logs(
            stdout=True,
            stderr=True,
            timestamps=timestamps,
            tail=tail,
            since=since,
            follow=follow,
        )
        return self._decode_service_logs(stream)

    @staticmethod
    def _decode_service_logs(stream) -> Iterator[str]:
        if isinstance(stream, bytes):
            yield from stream.decode("utf-8", "replace").splitlines()
            return
        try:
            for chunk in stream:
                text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)
                yield from text.splitlines()
        except (DockerException, requests.exceptions.RequestException) as exc:
            raise OrchestratorError(f"read service logs: {_explain(exc)}") from exc
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()

    @_wrap("roll back service")
    def rollback_service(self, name_or_id: str) -> None:
        service = self.client.services.get(name_or_id)
        version = service.attrs["Version"]["Index"]
        # The Docker API takes ``rollback=previous`` as a query parameter, which
        # docker-py does not expose on update_service. Fall back to the private
        # helper rather than reimplementing the whole payload.
        response = self.api._post_json(
            self.api._url(f"/services/{service.id}/update"),
            data=service.attrs["Spec"],
            params={"version": version, "rollback": "previous"},
        )
        self.api._raise_for_status(response)

    @_wrap("read previous service labels")
    def previous_service_labels(self, name_or_id: str) -> dict[str, str]:
        service = self.client.services.get(name_or_id)
        return (service.attrs.get("PreviousSpec") or {}).get("Labels") or {}

    # ------------------------------------------------------------------ #
    # Secrets, configs, volumes
    # ------------------------------------------------------------------ #

    @_wrap("create secret")
    def create_secret(
        self, name: str, data: bytes, *, labels: dict[str, str] | None = None
    ) -> str:
        secret = self.client.secrets.create(
            name=name, data=data, labels={**_MANAGED_LABEL, **(labels or {})}
        )
        return secret.id

    @_wrap("remove secret")
    def remove_secret(self, name: str) -> None:
        try:
            self.client.secrets.get(name).remove()
        except NotFound:
            return

    @_wrap("create config")
    def create_config(
        self, name: str, data: bytes, *, labels: dict[str, str] | None = None
    ) -> str:
        config = self.client.configs.create(
            name=name, data=data, labels={**_MANAGED_LABEL, **(labels or {})}
        )
        return config.id

    @_wrap("remove config")
    def remove_config(self, name: str) -> None:
        try:
            self.client.configs.get(name).remove()
        except NotFound:
            return

    @_wrap("create volume")
    def create_volume(self, name: str, *, labels: dict[str, str] | None = None) -> None:
        try:
            self.client.volumes.get(name)
        except NotFound:
            self.client.volumes.create(
                name=name, driver="local", labels={**_MANAGED_LABEL, **(labels or {})}
            )

    @_wrap("remove volume")
    def remove_volume(self, name: str) -> None:
        try:
            self.client.volumes.get(name).remove()
        except NotFound:
            return

    # ------------------------------------------------------------------ #
    # Nodes
    # ------------------------------------------------------------------ #

    @_wrap("list nodes")
    def list_nodes(self) -> list[NodeInfo]:
        return sorted(
            (self._node_info(node.attrs) for node in self.client.nodes.list()),
            key=lambda node: node.hostname,
        )

    @_wrap("update node")
    def update_node(
        self,
        node_id: str,
        *,
        availability: str | None = None,
        labels: dict[str, str] | None = None,
        role: str | None = None,
    ) -> None:
        node = self.client.nodes.get(node_id)
        node_spec = dict(node.attrs["Spec"])
        if availability is not None:
            node_spec["Availability"] = availability
        if labels is not None:
            node_spec["Labels"] = labels
        if role is not None:
            node_spec["Role"] = role
        node.update(node_spec)

    @_wrap("remove node")
    def remove_node(self, node_id: str, *, force: bool = False) -> None:
        self.client.nodes.get(node_id).remove(force=force)

    @_wrap("read join tokens")
    def join_tokens(self) -> dict[str, str]:
        swarm = self.api.inspect_swarm()
        tokens = swarm.get("JoinTokens") or {}
        return {
            "Worker": tokens.get("Worker", ""),
            "Manager": tokens.get("Manager", ""),
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _with_network_ids(self, task_template: dict) -> dict:
        """Swarm accepts network names, but ids are unambiguous and cheaper."""
        networks = task_template.get("Networks")
        if not networks:
            return task_template
        resolved = []
        for entry in networks:
            target = entry["Target"]
            if target.startswith("net_"):
                resolved.append(entry)
                continue
            matches = self.client.networks.list(names=[target])
            resolved.append({"Target": matches[0].id if matches else target})
        task_template = dict(task_template)
        task_template["Networks"] = resolved
        return task_template

    def _resolve_file_refs(self, template: dict) -> None:
        container = template["ContainerSpec"]
        for field, kind, collection_name in (
            ("Secrets", "Secret", "secrets"), ("Configs", "Config", "configs")
        ):
            for ref in container.get(field, []):
                name = ref[f"{kind}Name"]
                try:
                    ref[f"{kind}ID"] = getattr(self.client, collection_name).get(name).id
                except NotFound:
                    raise OrchestratorError(f"Swarm {kind.lower()} {name!r} does not exist") from None

    @staticmethod
    def _service_info(attrs: dict) -> ServiceInfo:
        spec = attrs.get("Spec") or {}
        task_template = spec.get("TaskTemplate") or {}
        container = task_template.get("ContainerSpec") or {}
        status = attrs.get("ServiceStatus") or {}
        mode = spec.get("Mode") or {}
        endpoint = attrs.get("Endpoint") or {}
        ports = []
        for port in endpoint.get("Ports") or []:
            published = port.get("PublishedPort")
            target = port.get("TargetPort")
            protocol = port.get("Protocol", "tcp")
            ports.append(
                f"{published}->{target}/{protocol}" if published else f"{target}/{protocol}"
            )
        return ServiceInfo(
            id=attrs.get("ID", ""),
            name=spec.get("Name", ""),
            image=container.get("Image", ""),
            mode="global" if "Global" in mode else "replicated",
            replicas_desired=int(
                (mode.get("Replicated") or {}).get("Replicas", status.get("DesiredTasks", 0))
            ),
            replicas_running=int(status.get("RunningTasks", 0)),
            version=int((attrs.get("Version") or {}).get("Index", 0)),
            update_state=(attrs.get("UpdateStatus") or {}).get("State", ""),
            labels=dict(spec.get("Labels") or {}),
            ports=tuple(ports),
        )

    @staticmethod
    def _node_info(attrs: dict) -> NodeInfo:
        spec = attrs.get("Spec") or {}
        status = attrs.get("Status") or {}
        description = attrs.get("Description") or {}
        platform = description.get("Platform") or {}
        resources = description.get("Resources") or {}
        engine = description.get("Engine") or {}
        manager = attrs.get("ManagerStatus") or {}
        return NodeInfo(
            id=attrs.get("ID", ""),
            hostname=description.get("Hostname") or spec.get("Name", ""),
            role=spec.get("Role", "worker"),
            availability=spec.get("Availability", "active"),
            state=status.get("State", "unknown"),
            address=status.get("Addr", ""),
            architecture=platform.get("Architecture", ""),
            os=platform.get("OS", ""),
            cpu_cores=int(resources.get("NanoCPUs", 0)) // 1_000_000_000,
            memory_mb=int(resources.get("MemoryBytes", 0)) // (1024 * 1024),
            docker_version=engine.get("EngineVersion", ""),
            labels=dict(spec.get("Labels") or {}),
            is_leader=bool(manager.get("Leader", False)),
        )
