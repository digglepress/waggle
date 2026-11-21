"""Translate :mod:`orchestrator.spec` objects into Docker API payloads.

Docker's service model has a few sharp edges that are easy to get wrong:

* ``Resources``, ``Placement``, ``RestartPolicy`` and ``Networks`` live on the
  **TaskTemplate**, not inside ``ContainerSpec``.
* Service labels (``Spec.Labels``) are what Traefik's swarm provider reads.
  Container labels are a different thing.
* Every duration is nanoseconds.

Keeping the conversion in one pure module means it can be tested without a
Docker daemon, which is exactly what the test suite does.
"""

from __future__ import annotations

from typing import Any

from .spec import FileRef, ServiceSpec

NS = 1_000_000_000


def to_ns(seconds: float) -> int:
    return int(round(seconds * NS))


def to_nano_cpus(cpus: float) -> int:
    return int(round(cpus * 1_000_000_000))


def to_bytes(megabytes: int) -> int:
    return int(megabytes) * 1024 * 1024


def container_spec(spec: ServiceSpec) -> dict[str, Any]:
    container: dict[str, Any] = {
        "Image": spec.image,
        "StopGracePeriod": to_ns(spec.stop_grace_period_s),
    }
    if spec.init:
        container["Init"] = True
    if spec.env:
        container["Env"] = [f"{key}={value}" for key, value in spec.env.items()]
    if spec.container_labels:
        container["Labels"] = dict(spec.container_labels)
    if spec.command is not None:
        container["Command"] = list(spec.command)
    if spec.args is not None:
        container["Args"] = list(spec.args)
    if spec.user:
        container["User"] = spec.user
    if spec.working_dir:
        container["Dir"] = spec.working_dir
    if spec.hostname:
        container["Hostname"] = spec.hostname
    if spec.mounts:
        container["Mounts"] = [_mount(mount) for mount in spec.mounts]
    if spec.secrets:
        container["Secrets"] = [_file_ref(ref, "SecretName") for ref in spec.secrets]
    if spec.configs:
        container["Configs"] = [_file_ref(ref, "ConfigName") for ref in spec.configs]
    if spec.healthcheck:
        container["Healthcheck"] = {
            "Test": ["CMD-SHELL", spec.healthcheck.command],
            "Interval": to_ns(spec.healthcheck.interval_s),
            "Timeout": to_ns(spec.healthcheck.timeout_s),
            "StartPeriod": to_ns(spec.healthcheck.start_period_s),
            "Retries": spec.healthcheck.retries,
        }
    return container


def _mount(mount) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "Type": mount.type,
        "Target": mount.target,
        "ReadOnly": mount.read_only,
    }
    if mount.source:
        payload["Source"] = mount.source
    return payload


def _file_ref(ref: FileRef, name_key: str) -> dict[str, Any]:
    return {
        name_key: ref.name,
        "File": {
            "Name": ref.target or ref.name,
            "UID": ref.uid,
            "GID": ref.gid,
            "Mode": ref.mode,
        },
    }


def resources_spec(spec: ServiceSpec) -> dict[str, Any]:
    limits: dict[str, int] = {}
    reservations: dict[str, int] = {}
    if spec.resources.cpus is not None:
        limits["NanoCPUs"] = to_nano_cpus(spec.resources.cpus)
    if spec.resources.memory_mb is not None:
        limits["MemoryBytes"] = to_bytes(spec.resources.memory_mb)
    if spec.resources.reserve_cpus is not None:
        reservations["NanoCPUs"] = to_nano_cpus(spec.resources.reserve_cpus)
    if spec.resources.reserve_memory_mb is not None:
        reservations["MemoryBytes"] = to_bytes(spec.resources.reserve_memory_mb)

    payload: dict[str, Any] = {}
    if limits:
        payload["Limits"] = limits
    if reservations:
        payload["Reservations"] = reservations
    return payload


def restart_policy(spec: ServiceSpec) -> dict[str, Any]:
    policy = spec.restart_policy
    return {
        "Condition": policy.condition,
        "Delay": to_ns(policy.delay_s),
        "MaxAttempts": policy.max_attempts,
        "Window": to_ns(policy.window_s),
    }


def placement_spec(spec: ServiceSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if spec.placement.constraints:
        payload["Constraints"] = list(spec.placement.constraints)
    if spec.placement.preferences:
        payload["Preferences"] = [
            {"Spread": {"SpreadDescriptor": descriptor}}
            for strategy, descriptor in spec.placement.preferences
            if strategy == "spread"
        ]
    return payload


def task_template(spec: ServiceSpec) -> dict[str, Any]:
    template: dict[str, Any] = {
        "ContainerSpec": container_spec(spec),
        "RestartPolicy": restart_policy(spec),
        "LogDriver": {
            "Name": "json-file",
            "Options": {"max-size": spec.log_max_size, "max-file": str(spec.log_max_files)},
        },
    }
    resources = resources_spec(spec)
    if resources:
        template["Resources"] = resources
    placement = placement_spec(spec)
    if placement:
        template["Placement"] = placement
    if spec.networks:
        template["Networks"] = [{"Target": network} for network in spec.networks]
    return template


def service_mode(spec: ServiceSpec) -> dict[str, Any]:
    if spec.global_mode:
        return {"Global": {}}
    return {"Replicated": {"Replicas": spec.replicas}}


def update_config(spec: ServiceSpec) -> dict[str, Any]:
    config = spec.update_config
    return {
        "Parallelism": config.parallelism,
        "Delay": to_ns(config.delay_s),
        "Monitor": to_ns(config.monitor_s),
        "FailureAction": config.failure_action,
        "Order": config.order,
        "MaxFailureRatio": config.max_failure_ratio,
    }


def rollback_config(spec: ServiceSpec) -> dict[str, Any] | None:
    if spec.rollback_config is None:
        return None
    config = spec.rollback_config
    return {
        "Parallelism": config.parallelism,
        "Delay": to_ns(config.delay_s),
        "Monitor": to_ns(config.monitor_s),
        "FailureAction": config.failure_action,
        "Order": config.order,
        "MaxFailureRatio": config.max_failure_ratio,
    }


def endpoint_spec(spec: ServiceSpec) -> dict[str, Any] | None:
    if not spec.ports:
        return None
    ports: list[dict[str, Any]] = []
    for port in spec.ports:
        entry: dict[str, Any] = {
            "Protocol": port.protocol,
            "TargetPort": port.target,
            "PublishMode": port.mode,
        }
        if port.published is not None:
            entry["PublishedPort"] = port.published
        ports.append(entry)
    return {"Mode": "vip", "Ports": ports}


def create_kwargs(spec: ServiceSpec) -> dict[str, Any]:
    """Payload for ``POST /services/create``."""
    payload: dict[str, Any] = {
        "name": spec.name,
        "labels": dict(spec.labels),
        "task_template": task_template(spec),
        "mode": service_mode(spec),
        "update_config": update_config(spec),
    }
    rollback = rollback_config(spec)
    if rollback:
        payload["rollback_config"] = rollback
    endpoint = endpoint_spec(spec)
    if endpoint:
        payload["endpoint_spec"] = endpoint
    return payload


def update_kwargs(spec: ServiceSpec) -> dict[str, Any]:
    """Payload for ``POST /services/{id}/update`` (caller adds ``version``)."""
    payload = create_kwargs(spec)
    payload.pop("name", None)
    return payload
