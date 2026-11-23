import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import audit
from apps.core.logs import deployment_log_path
from apps.core.orchestration import get_orchestrator
from apps.deployments.domains import DomainsForm, service_hostnames, service_tls_enabled
from apps.deployments.envvars import TextVariablesForm, VariablesFormSet, format_dotenv
from apps.deployments.forms import ApplicationForm, ConnectionForm, NameForm
from apps.deployments.live_logs import deployment_events, response, runtime_events, runtime_tail
from apps.deployments.models import (
    ACTIVE_DEPLOYMENT_STATES,
    Application,
    Deployment,
    Environment,
    Project,
    SourceConnection,
    Tag,
)
from apps.deployments.services import owned_service, service_name, snapshot
from apps.deployments.tasks import deploy_application
from orchestrator.errors import OrchestratorError

logger = logging.getLogger("waggle.deployments")
staff_required = user_passes_test(lambda user: user.is_active and user.is_staff)


@login_required
def index(request):
    tag = request.GET.get("tag", "").strip().lower()[:36]
    matches = Application.objects.filter(tags__name=tag).select_related("scope__project") if tag else []
    return render(request, "deployments/index.html", {
        "projects": Project.objects.all(), "tags": Tag.objects.filter(application__isnull=False).distinct(),
        "tag": tag, "matches": matches,
    })


@staff_required
@require_http_methods(["GET", "POST"])
def new_project(request):
    return _name(request)


@login_required
def project(request, pk):
    item = get_object_or_404(Project, pk=pk)
    return render(request, "deployments/project.html", {"project": item})


@staff_required
@require_http_methods(["GET", "POST"])
def new_environment(request, project_id):
    return _name(request, get_object_or_404(Project, pk=project_id))


def _name(request, parent=None):
    form = NameForm(request.POST if request.method == "POST" else None, project=parent)
    if request.method == "POST" and form.is_valid():
        try:
            item = form.save()
        except IntegrityError:
            form.add_error("name", "A name with this URL slug already exists.")
        else:
            audit.record(
                request,
                "environment.create" if parent else "project.create",
                target=item,
            )
            return redirect("deployments:environment" if parent else "deployments:project", pk=item.pk)
    return render(request, "deployments/form.html", {
        "form": form, "heading": "New environment" if parent else "New project", "project": parent,
    })


@login_required
def environment(request, pk):
    scope = get_object_or_404(Environment.objects.select_related("project"), pk=pk)
    apps = list(scope.services.prefetch_related("tags"))
    statuses = {}
    error = ""
    if apps:
        try:
            statuses = {service.name: service for service in get_orchestrator().list_services()}
        except OrchestratorError:
            error = "Swarm status is unavailable right now."
    return render(request, "deployments/environment.html", {
        "scope": scope, "error": error,
        "rows": [(app, statuses.get(service_name(app))) for app in apps],
    })


@staff_required
def choose_source(request, env_id):
    scope = get_object_or_404(Environment.objects.select_related("project"), pk=env_id)
    return render(request, "deployments/choose_source.html", {"scope": scope})


@staff_required
def connections(request):
    return render(request, "deployments/connections.html", {
        "connections": SourceConnection.objects.defer("credential"),
    })


@staff_required
@require_http_methods(["GET", "POST"])
def new_connection(request, kind):
    label = _kind_label(kind)
    form = ConnectionForm(request.POST if request.method == "POST" else None, kind=kind)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        audit.record(request, "connection.create", target=item,
                     detail={"kind": item.kind, "host": item.host})
        env = request.GET.get("environment", "")
        if env.isascii() and env.isdecimal() and len(env) < 19 and Environment.objects.filter(pk=env).exists():
            return redirect("deployments:new", env_id=env, kind=kind)
        return redirect("deployments:connections")
    return _connection_form(request, form, f"Connect {label}")


@staff_required
@require_http_methods(["GET", "POST"])
def edit_connection(request, pk):
    item = get_object_or_404(SourceConnection, pk=pk)
    form = ConnectionForm(
        request.POST if request.method == "POST" else None, kind=item.kind, instance=item,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.record(request, "connection.update", target=item,
                     detail={"kind": item.kind, "host": item.host})
        messages.success(request, "Source connection saved.")
        return redirect("deployments:connections")
    return _connection_form(request, form, f"Edit {item.get_kind_display()}")


def _connection_form(request, form, heading):
    response = render(request, "deployments/form.html", {"form": form, "heading": heading})
    response["Cache-Control"] = "no-store"
    return response


def _kind_label(kind):
    try:
        return SourceConnection.Kind(kind).label
    except ValueError:
        raise Http404 from None


@staff_required
@require_http_methods(["GET", "POST"])
def edit(request, pk=None, env_id=None, kind=None):
    app = get_object_or_404(Application, pk=pk) if pk is not None else None
    if app:
        scope = app.scope
        kind = SourceConnection.Kind.GIT if app.repository else SourceConnection.Kind.REGISTRY
    else:
        scope = get_object_or_404(Environment.objects.select_related("project"), pk=env_id)
    label = _kind_label(kind)
    form = ApplicationForm(
        request.POST if request.method == "POST" else None, instance=app, scope=scope, kind=kind,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                item = form.save()
        except IntegrityError:
            form.add_error(None, "The service name or hostname is already in use.")
        else:
            audit.record(
                request,
                "service.update" if app else "service.create",
                target=item,
                detail={"kind": kind, "replicas": item.replicas, "port": item.port},
            )
            return redirect("deployments:detail" if app else "deployments:variables", pk=item.pk)
    return render(request, "deployments/form.html", {
        "form": form, "app": app, "scope": scope, "kind": kind,
        "heading": f"Edit {app.slug}" if app else f"New service · {label}",
        "needs_build_registry": kind == SourceConnection.Kind.GIT and not settings.REGISTRY_HOST,
    })


@staff_required
@require_http_methods(["GET", "POST"])
def variables(request, pk):
    app = get_object_or_404(Application, pk=pk)
    mode = request.GET.get("mode", "form")
    if mode not in {"form", "text"}:
        raise Http404
    data = request.POST if request.method == "POST" else None
    if mode == "text":
        form = TextVariablesForm(data, initial={"variables": format_dotenv(app.environment)})
    else:
        initial = [{"name": name, "value": value} for name, value in app.environment.items()]
        form = VariablesFormSet(data, initial=initial, prefix="variables")
    if data is not None and form.is_valid():
        app.environment = form.cleaned_data["variables"] if mode == "text" else form.variables
        app.save(update_fields=["environment", "updated_at"])
        audit.record(request, "service.variables", target=app,
                     detail={"mode": mode, "keys": sorted(app.environment)})
        messages.success(request, "Runtime variables saved. Deploy the service to apply them.")
        return redirect("deployments:detail", pk=pk)
    response = render(request, "deployments/variables.html", {"app": app, "form": form, "mode": mode})
    response["Cache-Control"] = "no-store"
    return response


@login_required
def detail(request, pk):
    app = get_object_or_404(Application, pk=pk)
    service, cluster_error = None, None
    if app.deployments.filter(
        state__in=(*ACTIVE_DEPLOYMENT_STATES, Deployment.State.SUBMITTED)
    ).exists():
        try:
            service = owned_service(get_orchestrator(), app)
        except OrchestratorError as exc:
            cluster_error = str(exc)
    return render(request, "deployments/detail.html", {
        "app": app, "jobs": app.deployments.defer("spec")[:5],
        "service": service, "cluster_error": cluster_error,
        "hostnames": sorted(service_hostnames(service.labels)) if service else [],
        "active_tls": service_tls_enabled(service.labels) if service else False,
    })


@staff_required
@require_http_methods(["GET", "POST"])
def domains(request, pk):
    app = get_object_or_404(Application.objects.select_related("scope__project"), pk=pk)
    form = DomainsForm(request.POST if request.method == "POST" else None, app=app)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("hostnames", "A hostname was assigned to another service. Refresh and try again.")
        else:
            audit.record(request, "service.domains", target=app,
                         detail={"hostnames": list(app.domains.values_list("hostname", flat=True))})
            messages.success(request, "Domains saved. Deploy the service to update its routing.")
            return redirect("deployments:domains", pk=pk)
    active_tls = False
    try:
        service = owned_service(get_orchestrator(), app)
        active = sorted(service_hostnames(service.labels)) if service else []
        active_tls = service_tls_enabled(service.labels) if service else False
    except OrchestratorError:
        active = None
    configured = list(app.domains.values_list("hostname", flat=True))
    return render(request, "deployments/domains.html", {
        "app": app, "form": form, "active": active,
        "has_pending": active is not None and configured != active,
        "active_tls": active_tls, "route_tls": settings.ROUTE_TLS,
        "cert_resolver": settings.TRAEFIK_CERT_RESOLVER,
    })


@staff_required
def history(request, pk):
    app = get_object_or_404(Application, pk=pk)
    page = Paginator(app.deployments.defer("spec"), 25).get_page(request.GET.get("page"))
    return render(request, "deployments/history.html", {"app": app, "page": page})


@staff_required
@require_POST
def launch(request, pk):
    return _queue(request, pk, Deployment.Operation.DEPLOY)


@staff_required
@require_POST
def revert(request, pk):
    return _queue(request, pk, Deployment.Operation.ROLLBACK)


@staff_required
@require_POST
def revert_to(request, pk, job_id):
    target = get_object_or_404(
        Deployment.objects.exclude(image=""), pk=job_id, application_id=pk,
        state=Deployment.State.SUBMITTED,
    )
    return _queue(request, pk, Deployment.Operation.ROLLBACK, target=target)


def _queue(request, pk, operation, *, target=None):
    app = get_object_or_404(Application, pk=pk)
    try:
        with transaction.atomic():
            job = Deployment.objects.create(
                application=app, operation=operation, spec=target.spec if target else snapshot(app),
                rollback_target=target, image=target.image if target else "",
            )
    except IntegrityError:
        messages.error(request, "This application already has a deployment in progress.")
        return redirect("deployments:detail", pk=pk)
    audit.record(
        request,
        f"deployment.{operation}",
        target=app,
        detail={
            "deployment_id": job.pk,
            "rollback_target": target.pk if target else None,
        },
    )
    try:
        deploy_application(job.pk)
    except Exception:
        logger.exception("Could not enqueue deployment %s", job.pk)
        Deployment.objects.filter(pk=job.pk, state=Deployment.State.QUEUED).update(
            state=Deployment.State.FAILED, error="Could not queue deployment. Check the worker logs.",
        )
        messages.error(request, "Could not queue deployment.")
    return redirect("deployments:job", pk=pk, job_id=job.pk)


@staff_required
@require_http_methods(["GET", "POST"])
def notifications(request):
    if request.method == "POST":
        request.session["notifications_read_at"] = timezone.now().isoformat()
        return redirect("deployments:notifications")
    seen = parse_datetime(request.session.get("notifications_read_at", ""))
    events = Deployment.objects.filter(
        state__in=[Deployment.State.SUBMITTED, Deployment.State.FAILED],
    ).select_related("application").defer("spec").order_by("-updated_at")
    page = Paginator(events, 25).get_page(request.GET.get("page"))
    return render(request, "deployments/notifications.html", {"page": page, "seen": seen})


@staff_required
def tasks(request, pk):
    app = get_object_or_404(Application.objects.select_related("scope__project"), pk=pk)
    rows, error = [], ""
    try:
        orchestrator = get_orchestrator()
        service = owned_service(orchestrator, app)
        if service:
            nodes = {node.id: node.hostname for node in orchestrator.list_nodes()}
            rows = [(task, nodes.get(task.node_id, task.node_id))
                    for task in orchestrator.service_tasks(service.id)
                    if task.desired_state == "running"]
    except OrchestratorError:
        error = "Container status is unavailable right now."
    return render(request, "deployments/tasks.html", {"app": app, "rows": rows, "error": error})


@staff_required
def logs(request, pk):
    app = get_object_or_404(Application, pk=pk)
    output, error = "", ""
    try:
        orchestrator = get_orchestrator()
        service = owned_service(orchestrator, app)
        if service is None:
            error = "No Swarm service found."
        else:
            output = "\n".join(runtime_tail(orchestrator, service.id))[-64 * 1024:]
    except OrchestratorError as exc:
        error = str(exc)
    response = render(request, "deployments/logs.html", {
        "app": app, "output": output, "error": error,
    })
    response["Cache-Control"] = "no-store"
    return response


@staff_required
def runtime_stream(request, pk):
    app = get_object_or_404(Application, pk=pk)
    try:
        orchestrator = get_orchestrator()
        service = owned_service(orchestrator, app)
    except OrchestratorError:
        service = None
    if not service:
        raise Http404
    return response(runtime_events(orchestrator, service.id))


@staff_required
def deployment_stream(request, pk, job_id):
    item = get_object_or_404(Deployment.objects.defer("spec"), pk=job_id, application_id=pk)
    return response(deployment_events(item.pk, request.headers.get("Last-Event-ID", "")))


@staff_required
def job(request, pk, job_id):
    item = get_object_or_404(Deployment.objects.select_related("application"), pk=job_id,
                             application_id=pk)
    try:
        with deployment_log_path(item.pk).open("rb") as log:
            log.seek(max(0, log.seek(0, 2) - 64 * 1024))
            output = log.read(64 * 1024).decode("utf-8", "replace")
    except FileNotFoundError:
        output = ""
    response = render(request, "deployments/job.html", {"job": item, "output": output})
    response["Cache-Control"] = "no-store"
    return response
