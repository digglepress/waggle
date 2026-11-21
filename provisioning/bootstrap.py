"""The node bootstrap state machine.

Six idempotent steps take a bare Linux box to a labelled Swarm node. Every step
checks before it acts, so re-running a failed bootstrap is safe and is in fact
the normal recovery path.

This module is Django-free: the caller supplies a ``save`` callback that
persists whatever fields a step produced.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from orchestrator.base import Orchestrator
from orchestrator.errors import OrchestratorError
from orchestrator.types import NodeInfo

from .docker_install import ensure_docker
from .executor import Executor
from .firewall import ensure_firewall_ports
from .preflight import run_preflight
from .swarm import join as swarm_join
from .swarm import read_state

MANAGED_LABEL = {"waggle.managed": "true"}


class BootstrapError(RuntimeError):
    """A step failed in a way the operator has to fix."""


@dataclass
class BootstrapContext:
    executor: Executor
    orchestrator: Orchestrator
    log: Callable[[str], None]
    data: dict[str, Any]
    facts: dict[str, Any] = field(default_factory=dict)
    save: Callable[..., None] = lambda **_: None


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    title: str
    run: Callable[[BootstrapContext], None]


def _save(ctx: BootstrapContext, **fields: Any) -> None:
    ctx.save(**fields)


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def step_connect(ctx: BootstrapContext) -> None:
    connect = getattr(ctx.executor, "connect", None)
    if callable(connect):
        connect()
    hostname = ctx.executor.run("hostname", timeout=30).stdout.strip()
    ctx.facts["hostname"] = hostname
    fingerprint = getattr(ctx.executor, "host_fingerprint", "")
    if fingerprint:
        _save(ctx, ssh_host_key=fingerprint)
    ctx.log(f"connected to {hostname or 'host'}")


def step_preflight(ctx: BootstrapContext) -> None:
    report = run_preflight(
        ctx.executor,
        min_ram_mb=int(ctx.data.get("min_ram_mb", 1024)),
    )
    for check in report.checks:
        detail = f" — {check.detail}" if check.detail else ""
        ctx.log(f"[{check.status:4}] {check.label}{detail}")
    if not report.ok:
        failed = ", ".join(check.label for check in report.failures)
        raise BootstrapError(f"preflight failed: {failed}")

    ctx.facts.update(report.facts)
    _save(
        ctx,
        os_name=report.facts.get("os_name", "")[:128],
        arch=report.facts.get("arch", "")[:32],
        cpu_cores=report.facts.get("cpu_cores") or None,
        ram_mb=report.facts.get("ram_mb") or None,
        disk_gb=report.facts.get("disk_gb") or None,
    )
    for warning in report.warnings:
        ctx.log(f"warning: {warning.label}: {warning.detail}")


def step_docker(ctx: BootstrapContext) -> None:
    result = ensure_docker(ctx.executor, ctx.log, mtu=ctx.data.get("mtu"))
    ctx.facts["docker_version"] = result.version
    _save(ctx, docker_version=result.version)


def step_firewall(ctx: BootstrapContext) -> None:
    ensure_firewall_ports(ctx.executor, ctx.log)


def step_swarm(ctx: BootstrapContext) -> None:
    swarm = ctx.orchestrator.swarm_info()
    if swarm is None:
        raise BootstrapError(
            "the control plane is not part of a swarm yet — run deploy/install.sh first"
        )

    state = read_state(ctx.executor)
    if state.is_active:
        if state.cluster_id and state.cluster_id != swarm.cluster_id:
            raise BootstrapError(
                "this server already belongs to a different swarm "
                f"({state.cluster_id}); run 'docker swarm leave --force' on it first"
            )
        ctx.log("node is already part of this cluster")
    else:
        role = ctx.data.get("role", "worker")
        tokens = ctx.orchestrator.join_tokens()
        token = tokens.get("Manager" if role == "manager" else "Worker", "")
        if not token:
            raise BootstrapError(f"the manager did not return a {role} join token")
        ctx.log(f"joining the swarm as a {role}")
        swarm_join(
            ctx.executor,
            manager_addr=f"{swarm.node_addr}:2377",
            token=token,
            advertise_addr=ctx.data.get("advertise_addr") or None,
            log=ctx.log,
        )

    node = _wait_for_node(ctx, timeout=float(ctx.data.get("node_timeout_s", 90)))
    ctx.facts["node"] = node
    _save(ctx, swarm_node_id=node.id, cluster_id=swarm.cluster_id)
    ctx.log(f"registered as swarm node {node.id} ({node.hostname})")


def step_labels(ctx: BootstrapContext) -> None:
    node: NodeInfo | None = ctx.facts.get("node")
    if node is None:
        raise BootstrapError("no swarm node to label")
    labels = {
        **node.labels,
        **MANAGED_LABEL,
        "waggle.node": node.hostname,
        **ctx.data.get("labels", {}),
    }
    ctx.orchestrator.update_node(node.id, labels=labels)
    _save(
        ctx,
        status="ready",
        availability="active",
        last_seen=datetime.now(UTC),
        bootstrap_error="",
    )
    ctx.log(f"applied labels: {', '.join(sorted(labels))}")


STEPS: tuple[Step, ...] = (
    Step("connect", "Connecting over SSH", step_connect),
    Step("preflight", "Checking the host", step_preflight),
    Step("docker", "Installing Docker Engine", step_docker),
    Step("firewall", "Opening swarm ports", step_firewall),
    Step("swarm", "Joining the swarm", step_swarm),
    Step("labels", "Applying node labels", step_labels),
)

STEP_IDS = tuple(step.id for step in STEPS)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def run_bootstrap(ctx: BootstrapContext) -> None:
    ctx.log("starting bootstrap")
    for step in STEPS:
        ctx.log(f"==> {step.title}")
        step.run(ctx)
        _save(ctx, bootstrap_step=step.id)
    ctx.log("==> Done. Node is ready.")


def _wait_for_node(
    ctx: BootstrapContext, *, timeout: float = 90.0, interval: float = 2.0
) -> NodeInfo:
    hostname = str(ctx.facts.get("hostname", ""))
    address = str(ctx.data.get("address", ""))
    advertise = str(ctx.data.get("advertise_addr") or "")
    deadline = time.monotonic() + timeout
    interval = max(min(interval, timeout / 5), 0.01)
    seen: list[str] = []

    while time.monotonic() < deadline:
        try:
            nodes = ctx.orchestrator.list_nodes()
        except OrchestratorError as exc:
            ctx.log(f"waiting for the manager: {exc}")
            nodes = []
        for node in nodes:
            if hostname and node.hostname == hostname:
                return node
            if address and node.address == address:
                return node
            if advertise and node.address == advertise:
                return node
        seen = [f"{node.hostname} ({node.address})" for node in nodes]
        time.sleep(interval)

    raise BootstrapError(
        "the node joined but never appeared in 'docker node ls'"
        + (f" (visible nodes: {', '.join(seen)})" if seen else "")
    )
