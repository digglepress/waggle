from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from apps.servers.models import Server

from .orchestration import cluster_status


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cluster = cluster_status()
        servers = Server.objects.all()
        context.update(
            cluster=cluster,
            servers=servers[:8],
            server_total=servers.count(),
            server_ready=servers.filter(status=Server.Status.READY).count(),
            server_attention=servers.filter(
                status__in=[Server.Status.ERROR, Server.Status.OFFLINE]
            ).count(),
            local_server=servers.filter(is_local=True).first(),
        )
        return context


class ClusterView(LoginRequiredMixin, TemplateView):
    template_name = "core/cluster.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cluster"] = cluster_status(include_services=True)
        return context


@require_GET
def health(request):
    """Liveness probe. No auth, no side effects."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - a probe must answer, not crash
        return JsonResponse({"status": "error", "detail": str(exc)}, status=503)
    return JsonResponse({"status": "ok", "service": "waggle"})
