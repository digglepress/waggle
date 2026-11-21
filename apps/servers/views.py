from __future__ import annotations

import json
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView

from apps.core.logs import read_new_lines
from apps.core.orchestration import cluster_status
from orchestrator.errors import OrchestratorError

from .constants import STEP_IDS, STEP_TITLES
from .forms import ServerForm, SSHKeyForm
from .models import Server, SSHKey
from .services import (
    bootstrap_log_path,
    refresh_server,
    remove_server,
    set_availability,
)
from .tasks import bootstrap_server


class ServerListView(LoginRequiredMixin, ListView):
    model = Server
    template_name = "servers/list.html"
    context_object_name = "servers"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cluster"] = cluster_status()
        return context


class ServerCreateView(LoginRequiredMixin, CreateView):
    model = Server
    form_class = ServerForm
    template_name = "servers/form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_keys"] = SSHKey.objects.exists()
        return context

    def form_valid(self, form):
        self.object = form.save()
        self.object.status = Server.Status.PROVISIONING
        self.object.save(update_fields=["status", "updated_at"])
        bootstrap_server(self.object.pk)
        messages.info(
            self.request,
            f"Bootstrapping {self.object.name}. Watch the log on this page.",
        )
        return redirect(self.object.get_absolute_url())


class ServerDetailView(LoginRequiredMixin, DetailView):
    model = Server
    template_name = "servers/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        server: Server = self.object
        context["steps"] = [
            {
                "id": step_id,
                "title": STEP_TITLES[step_id],
                "state": _step_state(index, server),
            }
            for index, step_id in enumerate(STEP_IDS)
        ]
        context["stream_url"] = reverse("servers:log", args=[server.pk])
        context["log_exists"] = bootstrap_log_path(server).exists()
        context["is_busy"] = server.is_busy
        context["cluster"] = cluster_status()
        return context


def _step_state(index: int, server: Server) -> str:
    if index < server.step_index:
        return "done"
    if index == server.step_index and server.is_busy:
        return "active"
    return "pending"


@login_required
@require_POST
def server_action(request, pk: int):
    server = get_object_or_404(Server, pk=pk)
    action = request.POST.get("action", "")

    try:
        if action == "bootstrap":
            if server.is_local:
                raise OrchestratorError(
                    "the control-plane host is installed by deploy/install.sh, not bootstrapped"
                )
            server.status = Server.Status.PROVISIONING
            server.bootstrap_step = ""
            server.save(update_fields=["status", "bootstrap_step", "updated_at"])
            bootstrap_server(server.pk)
            messages.info(request, f"Bootstrap queued for {server.name}.")
        elif action == "refresh":
            refresh_server(server)
            messages.success(request, f"Refreshed {server.name} from the swarm.")
        elif action == "drain":
            set_availability(server, Server.Availability.DRAIN)
            messages.success(request, f"{server.name} is draining. Tasks are moving off it.")
        elif action == "activate":
            set_availability(server, Server.Availability.ACTIVE)
            messages.success(request, f"{server.name} is accepting tasks again.")
        elif action == "pause":
            set_availability(server, Server.Availability.PAUSE)
            messages.success(request, f"{server.name} is paused. Existing tasks keep running.")
        elif action == "remove":
            name = server.name
            remove_server(server)
            messages.success(request, f"{name} was removed from the swarm.")
            return redirect("servers:list")
        else:
            messages.error(request, f"Unknown action: {action!r}")
    except (OrchestratorError, ValueError, ConnectionError) as exc:
        messages.error(request, str(exc))

    return redirect(server.get_absolute_url())


@login_required
def server_log(request, pk: int):
    """Server-sent events carrying the bootstrap log.

    The worker writes lines to a file; this view tails it. No Redis, no
    Channels, and the stream survives a web-process restart.
    """
    server = get_object_or_404(Server, pk=pk)
    path = bootstrap_log_path(server)

    def events():
        position = 0
        started = time.monotonic()
        last_ping = started
        while True:
            lines, position = read_new_lines(path, position)
            for line in lines:
                yield f"data: {json.dumps(line)}\n\n"

            try:
                server.refresh_from_db(fields=["status"])
            except Server.DoesNotExist:
                yield "event: done\ndata: {}\n\n"
                return

            if not server.is_busy:
                yield "event: done\ndata: {}\n\n"
                return

            now = time.monotonic()
            if now - started >= settings.SSE_STREAM_SECONDS:
                yield "event: done\ndata: {}\n\n"
                return
            if now - last_ping >= 15:
                yield ": keepalive\n\n"
                last_ping = now
            time.sleep(0.5)

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-store"
    response["X-Accel-Buffering"] = "no"
    return response


class SSHKeyListView(LoginRequiredMixin, ListView):
    model = SSHKey
    template_name = "servers/keys.html"
    context_object_name = "keys"


class SSHKeyCreateView(LoginRequiredMixin, CreateView):
    model = SSHKey
    form_class = SSHKeyForm
    template_name = "servers/key_form.html"

    def get_success_url(self):
        return reverse("servers:keys")

    def form_valid(self, form):
        messages.success(self.request, "SSH key saved.")
        return super().form_valid(form)
