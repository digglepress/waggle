from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from apps.deployments.models import Application, Deployment
from apps.servers.models import Server

from .models import AuditEvent
from .orchestration import cluster_status
from .version import VERSION

logger = logging.getLogger("waggle.health")
staff_required = user_passes_test(lambda user: user.is_active and user.is_staff)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cluster = cluster_status(include_services=True)
        servers = Server.objects.all()
        context.update(
            cluster=cluster,
            active_services=[
                service for service in cluster.services if service.replicas_running > 0
            ],
            servers=servers[:8],
            server_total=servers.count(),
            server_ready=servers.filter(status=Server.Status.READY).count(),
            server_attention=servers.filter(
                status__in=[Server.Status.ERROR, Server.Status.OFFLINE]
            ).count(),
            local_server=servers.filter(is_local=True).first(),
        )
        context["show_onboarding"] = self.request.user.is_staff and not Application.objects.exists()
        return context


class ClusterView(LoginRequiredMixin, TemplateView):
    template_name = "core/cluster.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cluster"] = cluster_status(include_services=True)
        return context


@require_GET
def health(request):
    """Liveness probe. No auth, no side effects, no internal detail."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - a probe must answer, not crash
        logger.exception("Health check failed")
        return JsonResponse({"status": "error", "service": "waggle"}, status=503)
    return JsonResponse({"status": "ok", "service": "waggle", "version": VERSION})


@require_GET
def ready(request):
    """Readiness probe for a load balancer: the database must be usable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - a probe must answer, not crash
        logger.exception("Readiness check failed")
        return JsonResponse({"status": "not-ready", "service": "waggle"}, status=503)
    response = JsonResponse({"status": "ready", "service": "waggle", "version": VERSION})
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@require_GET
def metrics(request):
    """Prometheus gauges from persisted state; a scrape never calls Docker."""
    if not settings.METRICS_TOKEN:
        raise Http404
    if not constant_time_compare(
        request.headers.get("Authorization", ""), f"Bearer {settings.METRICS_TOKEN}"
    ):
        return HttpResponseForbidden()

    lines = [
        "# HELP waggle_applications Number of saved application definitions.",
        "# TYPE waggle_applications gauge",
        f"waggle_applications {Application.objects.count()}",
    ]
    for model, field, states, name, description in (
        (Server, "status", Server.Status, "waggle_servers", "Registered servers by last saved state."),
        (Deployment, "state", Deployment.State, "waggle_deployments", "Deployment records by state."),
    ):
        totals = dict(model.objects.order_by().values(field).annotate(total=Count("pk"))
                      .values_list(field, "total"))
        lines.extend([f"# HELP {name} {description}", f"# TYPE {name} gauge"])
        lines.extend(f'{name}{{state="{state}"}} {totals.get(state, 0)}' for state in states.values)
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; version=0.0.4; charset=utf-8")


@login_required
@staff_required
def audit_log(request):
    page = Paginator(
        AuditEvent.objects.select_related("actor").defer("detail"), 100,
    ).get_page(request.GET.get("page"))
    response = render(request, "core/audit.html", {"page": page})
    response["Cache-Control"] = "no-store"
    return response
