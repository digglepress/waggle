"""Small, token-authenticated JSON API for automation and CI."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from apps.core import audit
from apps.core.api_auth import require_token
from apps.deployments.models import Application, Deployment
from apps.deployments.queueing import (
    DeploymentConflict,
    DeploymentQueueError,
    queue_deployment,
)


def _deployment(job) -> dict:
    return {
        "id": job.pk,
        "operation": job.operation,
        "state": job.state,
        "state_label": job.get_state_display(),
        "image": job.image,
        "error": job.error,
        "rollback_target": job.rollback_target_id,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def _service(app) -> dict:
    latest = app.deployments.defer("spec").first()
    return {
        "id": app.pk,
        "slug": app.slug,
        "project": app.scope.project.slug,
        "environment": app.scope.slug,
        "image": app.image,
        "repository": app.repository,
        "revision": app.revision,
        "port": app.port,
        "replicas": app.replicas,
        "domains": sorted(app.domains.values_list("hostname", flat=True)),
        "tags": sorted(app.tags.values_list("name", flat=True)),
        "latest_deployment": _deployment(latest) if latest else None,
    }


def _body(request) -> dict:
    if not request.body:
        return {}
    try:
        value = json.loads(request.body)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _services(request):
    services = Application.objects.select_related("scope__project").prefetch_related(
        "domains", "tags",
    )
    project = request.GET.get("project", "").strip()
    environment = request.GET.get("environment", "").strip()
    if project:
        services = services.filter(scope__project__slug=project)
    if environment:
        services = services.filter(scope__slug=environment)
    return JsonResponse({"services": [_service(app) for app in services[:200]]})


def _service_detail(request, pk):
    app = get_object_or_404(
        Application.objects.select_related("scope__project"), pk=pk,
    )
    jobs = app.deployments.defer("spec")[:20]
    return JsonResponse({
        "service": _service(app),
        "deployments": [_deployment(job) for job in jobs],
    })


def _enqueue(request, app, operation, *, target=None):
    try:
        job = queue_deployment(app, operation, target=target)
    except DeploymentConflict:
        return JsonResponse(
            {"error": "This service already has a deployment in progress."}, status=409,
        )
    except DeploymentQueueError:
        return JsonResponse(
            {"error": "The deployment worker is unavailable."}, status=503,
        )
    audit.record(
        request,
        f"deployment.{operation}",
        target=app,
        detail={
            "deployment_id": job.pk,
            "rollback_target": target.pk if target else None,
            "via": "api",
            "token": request.api_token.name,
        },
    )
    return JsonResponse({"deployment": _deployment(job)}, status=202)


def _deploy(request, pk):
    app = get_object_or_404(Application, pk=pk)
    return _enqueue(request, app, Deployment.Operation.DEPLOY)


def _rollback(request, pk):
    app = get_object_or_404(Application, pk=pk)
    target = None
    deployment_id = _body(request).get("deployment_id")
    if deployment_id is not None:
        target = get_object_or_404(
            Deployment.objects.exclude(image=""),
            pk=deployment_id,
            application=app,
            state=Deployment.State.SUBMITTED,
        )
    return _enqueue(request, app, Deployment.Operation.ROLLBACK, target=target)


def _deployment_detail(request, pk):
    job = get_object_or_404(
        Deployment.objects.select_related("application__scope__project"), pk=pk,
    )
    return JsonResponse({
        "deployment": _deployment(job),
        "service": {
            "id": job.application_id,
            "slug": job.application.slug,
            "project": job.application.scope.project.slug,
            "environment": job.application.scope.slug,
        },
    })


def _me(request):
    token = request.api_token
    return JsonResponse({
        "name": token.name,
        "scopes": token.scopes,
        "prefix": token.prefix,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
    })


@require_http_methods(["GET"])
@require_token("read")
def services(request):
    return _services(request)


@require_http_methods(["GET"])
@require_token("read")
def service_detail(request, pk):
    return _service_detail(request, pk)


@require_http_methods(["POST"])
@require_token("deploy")
def deploy(request, pk):
    return _deploy(request, pk)


@require_http_methods(["POST"])
@require_token("deploy")
def rollback(request, pk):
    return _rollback(request, pk)


@require_http_methods(["GET"])
@require_token("read")
def deployment_detail(request, pk):
    return _deployment_detail(request, pk)


@require_http_methods(["GET"])
@require_token("read")
def me(request):
    return _me(request)
