import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

from apps.core.logs import deployment_log_path
from apps.core.orchestration import get_orchestrator
from apps.deployments.build_logs import run_build
from apps.deployments.domains import (
    DomainError,
    requested_domains,
    service_hostnames,
    validate_routing,
)
from apps.deployments.models import SourceConnection
from apps.deployments.sources import image_host, repository_host
from orchestrator.errors import OrchestratorError
from orchestrator.spec import FileRef, Healthcheck, Mount, RegistryAuth, Resources, ServiceSpec

DEPLOY_FIELDS = ("image", "repository", "revision", "port", "replicas",
                 "memory_mb", "cpu_millicores", "healthcheck", "environment", "secret_names",
                 "connection_id", "build_target", "volumes", "health_interval", "health_timeout",
                 "health_start_period", "health_retries")


class DeploymentError(RuntimeError):
    pass


def snapshot(app):
    return {
        **{key: getattr(app, key) for key in DEPLOY_FIELDS},
        "domains": list(app.domains.values_list("hostname", flat=True)),
        "submodule_connection_ids": list(app.submodule_connections.values_list("pk", flat=True)),
    }


def service_name(app):
    if app.legacy_service_name:
        return f"{settings.OBJECT_PREFIX}-app-{app.slug}"
    return f"{settings.OBJECT_PREFIX}-service-{app.pk}-{app.slug}"


def build_image(deployment, connection=None):
    registry = settings.REGISTRY_HOST
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?", registry):
        raise DeploymentError("Set WAGGLE_REGISTRY_HOST to a registry hostname and optional port.")
    name = f"{registry}/{service_name(deployment.application)}"
    source = deployment.spec
    context = f"{source['repository']}#{source['revision']}"
    log_path = deployment_log_path(deployment.pk)
    with tempfile.TemporaryDirectory(prefix="waggle-build-") as temp:
        metadata = Path(temp) / "metadata.json"
        command = [
            "docker", "buildx", "build", "--progress=plain", "--push",
            "--metadata-file", str(metadata), "--tag", f"{name}:deploy-{deployment.pk}",
        ]
        if source.get("build_target"):
            command.extend(["--target", source["build_target"]])
        submodules = SourceConnection.objects.filter(pk__in=source.get("submodule_connection_ids", []),
                                                      kind=SourceConnection.Kind.GIT)
        git_sources = ([connection] if connection else []) + list(submodules)
        build_env = None
        for index, git_source in enumerate(git_sources):
            if git_source.credential:
                key = "WAGGLE_GIT_AUTH_TOKEN" if index == 0 else f"WAGGLE_GIT_AUTH_TOKEN_{index}"
                if build_env is None:
                    build_env = dict(os.environ)
                build_env[key] = git_source.credential
                command.extend(["--secret", f"id=GIT_AUTH_TOKEN.{git_source.host},env={key}"])
        command.extend(["--", context])
        try:
            run_build(command, env=build_env, log_path=log_path, timeout=settings.BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise DeploymentError("Build timed out. See the build log.") from None
        except subprocess.CalledProcessError:
            raise DeploymentError("Build or push failed. See the build log.") from None
        except OSError:
            raise DeploymentError("Could not start Docker buildx or open the build log.") from None
        try:
            digest = json.loads(metadata.read_text(encoding="utf-8"))["containerimage.digest"]
        except (OSError, ValueError, KeyError, TypeError):
            raise DeploymentError("Buildx did not return an image digest.") from None
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise DeploymentError("Buildx did not return a valid image digest.")
        return f"{name}@{digest}"


def make_spec(deployment, image, registry_auth=None):
    app = deployment.application
    values = deployment.spec
    name = service_name(app)
    labels = {"waggle.managed": "true", "waggle.app-id": str(app.pk)}
    domains = requested_domains(values)
    if domains:
        router = f"traefik.http.routers.{name}"
        labels.update({
            "traefik.enable": "true",
            "traefik.swarm.network": settings.PROXY_NETWORK,
            "waggle.domains": ",".join(domains),
            f"{router}.rule": " || ".join(f"Host(`{hostname}`)" for hostname in domains),
            f"{router}.entrypoints": "websecure" if settings.ROUTE_TLS else "web",
            f"{router}.service": name,
            f"traefik.http.services.{name}.loadbalancer.server.port": str(values["port"]),
        })
        if settings.ROUTE_TLS:
            labels[f"{router}.tls"] = "true"
            if settings.TRAEFIK_CERT_RESOLVER:
                labels[f"{router}.tls.certresolver"] = settings.TRAEFIK_CERT_RESOLVER
                for index, hostname in enumerate(domains):
                    labels[f"{router}.tls.domains[{index}].main"] = hostname
    cpu = values.get("cpu_millicores")
    health = values.get("healthcheck")
    return ServiceSpec(
        name=name, image=image, replicas=values["replicas"], env=values["environment"],
        labels=labels, networks=(settings.PROXY_NETWORK,),
        container_labels={"waggle.managed": "true"},
        secrets=tuple(FileRef(name=secret) for secret in values["secret_names"]),
        resources=Resources(cpus=cpu / 1000 if cpu else None, memory_mb=values.get("memory_mb")),
        healthcheck=Healthcheck(
            command=health, interval_s=values.get("health_interval", 15),
            timeout_s=values.get("health_timeout", 5), start_period_s=values.get("health_start_period", 10),
            retries=values.get("health_retries", 3),
        ) if health else None,
        mounts=tuple(Mount(
            source=f"{name}-{volume['name']}", target=volume["target"],
            read_only=volume.get("read_only", False),
        ) for volume in values.get("volumes", [])),
        registry_auth=registry_auth,
    )


def deploy(deployment, *, resolved_image=None, on_converge=None):
    values = deployment.spec
    orchestrator = get_orchestrator()
    try:
        domains = requested_domains(values)
        validate_routing(orchestrator, deployment.application, domains)
    except DomainError as exc:
        raise DeploymentError(str(exc)) from exc
    connection = _deployment_connection(deployment)
    if values["image"]:
        image = resolved_image or values["image"]
        auth = RegistryAuth(connection.host, connection.username, connection.credential) if (
            connection and connection.credential
        ) else None
    else:
        image = resolved_image or build_image(deployment, connection)
        auth = None  # Built images use the worker's configured push/pull credentials.
    spec = make_spec(deployment, image, auth)
    # Recheck after a build; a domain may have been edited while it ran.
    if not values["image"] and domains:
        try:
            validate_routing(orchestrator, deployment.application, domains)
        except DomainError as exc:
            raise DeploymentError(str(exc)) from exc
    existing = owned_service(orchestrator, deployment.application)
    orchestrator.ensure_network(settings.PROXY_NETWORK)
    if existing:
        service_id = existing.id
        orchestrator.update_service(service_id, spec, force=True)
    else:
        service_id = orchestrator.create_service(spec)
    if on_converge:
        on_converge()
    _await_convergence(orchestrator, service_id, spec.replicas, updating=bool(existing))
    return image


def _await_convergence(orchestrator, service_id, replicas, *, updating):
    """Wait for the rollout to become healthy, rolling back a bad update."""
    result = orchestrator.wait_for_convergence(
        service_id, replicas=replicas, timeout=settings.DEPLOY_CONVERGE_TIMEOUT,
    )
    if result.converged:
        return
    reason = result.message or f"{result.running}/{result.desired} tasks running"
    if updating and settings.DEPLOY_AUTO_ROLLBACK:
        try:
            orchestrator.rollback_service(service_id)
        except OrchestratorError as exc:
            raise DeploymentError(
                f"Deployment did not converge ({reason}) and the automatic rollback failed: {exc}"
            ) from exc
        raise DeploymentError(
            f"Deployment did not converge ({reason}). Rolled back to the previous version."
        )
    raise DeploymentError(f"Deployment did not converge ({reason}).")


def _deployment_connection(deployment):
    values = deployment.spec
    connection_id = values.get("connection_id")
    if not connection_id:
        if deployment.application.legacy_service_name:
            return None
        raise DeploymentError("Connect a source before deploying this service.")
    connection = SourceConnection.objects.filter(pk=connection_id).first()
    is_image = bool(values["image"])
    kind = SourceConnection.Kind.REGISTRY if is_image else SourceConnection.Kind.GIT
    try:
        host = image_host(values["image"]) if is_image else repository_host(values["repository"])
    except ValueError as exc:
        raise DeploymentError(str(exc)) from exc
    if not connection or connection.kind != kind or connection.host != host:
        raise DeploymentError("The source does not match the selected connection.")
    return connection


def owned_service(orchestrator, app):
    service = orchestrator.get_service(service_name(app))
    if service and service.labels.get("waggle.app-id") != str(app.pk):
        raise OrchestratorError("A service with this name exists and is not owned by this app.")
    return service


def rollback(app):
    orchestrator = get_orchestrator()
    service = owned_service(orchestrator, app)
    if not service:
        raise DeploymentError("No Swarm service to roll back.")
    previous_domains = service_hostnames(orchestrator.previous_service_labels(service.id))
    try:
        validate_routing(orchestrator, app, sorted(previous_domains))
    except DomainError as exc:
        raise DeploymentError(str(exc)) from exc
    orchestrator.rollback_service(service.id)
