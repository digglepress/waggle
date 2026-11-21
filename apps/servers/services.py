"""Server operations: bootstrap, reconcile with the swarm, lifecycle actions.

Views stay thin; everything that talks to SSH or Docker lives here so it can be
called from a task, a management command or a test.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.core.logs import append_line, reset_log, server_log_path
from apps.core.orchestration import get_orchestrator
from orchestrator.errors import OrchestratorError
from orchestrator.types import NodeInfo
from provisioning.bootstrap import BootstrapContext, run_bootstrap
from provisioning.ssh import SSHExecutor

from .models import Server

logger = logging.getLogger("waggle.servers")


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def build_executor(server: Server) -> SSHExecutor:
    if server.is_local:
        raise ValueError("the control-plane host is bootstrapped by the installer, not over SSH")
    if server.ssh_key is None:
        raise ValueError("no SSH key is configured for this server")
    return SSHExecutor(
        host=server.address,
        port=server.ssh_port,
        user=server.ssh_user,
        private_key=server.ssh_key.private_key,
        connect_timeout=settings.SSH_CONNECT_TIMEOUT,
        command_timeout=settings.SSH_COMMAND_TIMEOUT,
        expected_fingerprint=server.ssh_host_key or None,
    )


def bootstrap_log_path(server: Server) -> Path:
    return server_log_path(server.pk)


def run_bootstrap_for(server: Server, *, orchestrator=None) -> None:
    """Run the bootstrap steps and keep the Server row in sync.

    Raises on failure after recording the error on the row, so the Huey task
    surfaces a failed job as well as a visible server state.
    """
    orchestrator = orchestrator or get_orchestrator()
    path = bootstrap_log_path(server)
    reset_log(path)

    def log(line: str) -> None:
        append_line(path, line)
        logger.info("[server %s] %s", server.pk, line)

    def save(**fields) -> None:
        for key, value in fields.items():
            setattr(server, key, value)
        server.save(update_fields=[*fields.keys(), "updated_at"])

    server.status = Server.Status.PROVISIONING
    server.bootstrap_step = ""
    server.bootstrap_error = ""
    server.last_error = ""
    server.save(
        update_fields=[
            "status",
            "bootstrap_step",
            "bootstrap_error",
            "last_error",
            "updated_at",
        ]
    )

    executor: SSHExecutor | None = None
    try:
        executor = build_executor(server)
        context = BootstrapContext(
            executor=executor,
            orchestrator=orchestrator,
            log=log,
            data={
                "name": server.name,
                "address": server.address,
                "role": server.role,
                "labels": server.labels or {},
                "advertise_addr": server.advertise_addr,
                "min_ram_mb": settings.NODE_MIN_RAM_MB,
            },
            save=save,
        )
        run_bootstrap(context)
    except Exception as exc:
        logger.exception("bootstrap failed for server %s", server.pk)
        message = str(exc) or exc.__class__.__name__
        log(f"ERROR: {message}")
        server.status = Server.Status.ERROR
        server.bootstrap_error = message[:4000]
        server.last_error = message[:4000]
        server.save(
            update_fields=["status", "bootstrap_error", "last_error", "updated_at"]
        )
        raise
    finally:
        if executor is not None:
            executor.close()


# --------------------------------------------------------------------------- #
# Reconcile
# --------------------------------------------------------------------------- #


def match_node(server: Server, nodes: list[NodeInfo]) -> NodeInfo | None:
    if server.swarm_node_id:
        for node in nodes:
            if node.id == server.swarm_node_id:
                return node
    for node in nodes:
        if node.hostname and node.hostname == server.name:
            return node
    for node in nodes:
        if node.address and node.address in {server.address, server.advertise_addr}:
            return node
    return None


def refresh_server(server: Server, *, nodes: list[NodeInfo] | None = None) -> Server:
    orchestrator = get_orchestrator()
    if nodes is None:
        nodes = orchestrator.list_nodes()
    node = match_node(server, nodes)

    if node is None:
        server.status = Server.Status.OFFLINE
        server.last_error = "node is not registered in the swarm"
        server.save(update_fields=["status", "last_error", "updated_at"])
        return server

    server.swarm_node_id = node.id
    server.role = node.role
    server.availability = node.availability
    server.status = Server.Status.READY if node.is_ready else Server.Status.OFFLINE
    server.labels = dict(node.labels)
    server.docker_version = node.docker_version or server.docker_version
    server.os_name = node.os or server.os_name
    server.arch = node.architecture or server.arch
    server.cpu_cores = node.cpu_cores or server.cpu_cores
    server.ram_mb = node.memory_mb or server.ram_mb
    server.last_seen = timezone.now()
    if node.is_ready:
        server.last_error = ""
    server.save(
        update_fields=[
            "swarm_node_id",
            "role",
            "availability",
            "status",
            "labels",
            "docker_version",
            "os_name",
            "arch",
            "cpu_cores",
            "ram_mb",
            "last_seen",
            "last_error",
            "updated_at",
        ]
    )
    return server


def sync_local_node() -> Server | None:
    """Register or refresh the host this control plane runs on."""
    orchestrator = get_orchestrator()
    try:
        info = orchestrator.swarm_info()
        if info is None:
            logger.info("local host is not part of a swarm; skipping registration")
            return None
        nodes = orchestrator.list_nodes()
    except OrchestratorError as exc:
        logger.warning("cannot sync the local node: %s", exc)
        return None

    node = next((item for item in nodes if item.id == info.node_id), None)
    name = (node.hostname if node else info.hostname) or "control-plane"

    existing = Server.objects.filter(is_local=True).first()
    if existing and existing.name != name:
        clash = Server.objects.filter(name=name).exclude(pk=existing.pk).exists()
        if clash:
            name = f"{name}-control"

    defaults = {
        "name": name,
        "address": (node.address if node else info.node_addr) or "127.0.0.1",
        "role": Server.Role.MANAGER if info.control_available else Server.Role.WORKER,
        "status": Server.Status.READY,
        "availability": node.availability if node else Server.Availability.ACTIVE,
        "swarm_node_id": info.node_id,
        "cluster_id": info.cluster_id,
        "docker_version": node.docker_version if node else "",
        "os_name": node.os if node else "",
        "arch": node.architecture if node else "",
        "cpu_cores": node.cpu_cores if node else None,
        "ram_mb": node.memory_mb if node else None,
        "last_seen": timezone.now(),
        "last_error": "",
    }
    server, created = Server.objects.update_or_create(is_local=True, defaults=defaults)
    logger.info("%s local node %s", "registered" if created else "refreshed", server.name)
    return server


# --------------------------------------------------------------------------- #
# Lifecycle actions
# --------------------------------------------------------------------------- #


def set_availability(server: Server, availability: str) -> Server:
    if availability not in Server.Availability.values:
        raise ValueError(f"unknown availability: {availability}")
    if not server.swarm_node_id:
        raise OrchestratorError("this server is not a registered swarm node yet")

    get_orchestrator().update_node(server.swarm_node_id, availability=availability)
    server.availability = availability
    server.status = (
        Server.Status.DRAINING if availability == Server.Availability.DRAIN else Server.Status.READY
    )
    server.save(update_fields=["availability", "status", "updated_at"])
    return server


def remove_server(server: Server) -> None:
    if server.is_local:
        raise OrchestratorError("the control-plane host cannot be removed from Waggle")
    if server.swarm_node_id:
        orchestrator = get_orchestrator()
        orchestrator.update_node(server.swarm_node_id, availability="drain")
        orchestrator.remove_node(server.swarm_node_id, force=True)
    server.delete()


def reconcile_all() -> dict[str, int]:
    """Refresh every server from the swarm. Used by ``manage.py sync_cluster``."""
    summary = {"local": 0, "refreshed": 0, "offline": 0, "failed": 0}
    if sync_local_node() is not None:
        summary["local"] = 1

    orchestrator = get_orchestrator()
    try:
        nodes = orchestrator.list_nodes()
    except OrchestratorError as exc:
        logger.warning("cannot list nodes: %s", exc)
        summary["failed"] = Server.objects.filter(is_local=False).count()
        return summary

    for server in Server.objects.filter(is_local=False):
        try:
            refresh_server(server, nodes=nodes)
        except OrchestratorError as exc:
            logger.warning("cannot refresh %s: %s", server.name, exc)
            summary["failed"] += 1
            continue
        if server.status == Server.Status.OFFLINE:
            summary["offline"] += 1
        else:
            summary["refreshed"] += 1
    return summary
