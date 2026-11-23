import hashlib
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.core import audit
from apps.core.locks import LockBusy, operation_lock
from apps.core.orchestration import get_orchestrator
from apps.deployments.container_files import (
    BrowserError,
    browse_path,
    file_command,
    parse_file_response,
)
from apps.deployments.models import Application
from apps.deployments.terminal import TerminalError, run_terminal, select_tasks
from orchestrator.errors import OrchestratorError

logger = logging.getLogger("waggle.terminal")
COMMAND_LIMIT = 4096


def require_operator(request):
    if not request.user.is_staff or not request.user.is_active:
        raise PermissionDenied


@never_cache
@login_required
@require_GET
def terminal(request, pk):
    return access_page(request, pk, template="deployments/terminal.html")


@never_cache
@login_required
@require_GET
def files(request, pk):
    return access_page(request, pk, template="deployments/files.html")


def access_page(request, pk, *, template):
    require_operator(request)
    app = get_object_or_404(Application.objects.select_related("scope__project"), pk=pk)
    rows, error = [], ""
    if settings.WEB_TERMINAL:
        try:
            orchestrator = get_orchestrator()
            tasks = select_tasks(orchestrator, app)
            if tasks:
                names = {item.id: item.hostname for item in orchestrator.list_nodes()}
                rows = [(item, names.get(item.node_id, item.node_id)) for item in tasks]
        except OrchestratorError:
            error = "Could not read running replicas from Swarm."
    return render(request, template, {
        "app": app, "rows": rows, "error": error, "enabled": settings.WEB_TERMINAL,
        "command_limit": COMMAND_LIMIT,
    })


@never_cache
@login_required
@require_POST
def execute(request, pk):
    require_operator(request)
    if not settings.WEB_TERMINAL:
        raise PermissionDenied
    app = get_object_or_404(Application, pk=pk)
    command = request.POST.get("command", "")
    task_id = request.POST.get("task", "")
    if not task_id or len(task_id) > 64 or not command.strip() or "\0" in command or len(command) > COMMAND_LIMIT:
        return JsonResponse({"error": "Select a replica and enter a command of up to 4096 characters."}, status=400)
    try:
        with operation_lock("container-terminal"):
            result = run_terminal(get_orchestrator(), app, task_id, command)
    except LockBusy:
        return JsonResponse({"error": "Another terminal command is running. Try again shortly."}, status=429)
    except TerminalError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except (OrchestratorError, OSError):
        return JsonResponse({"error": "Could not contact the target Docker node."}, status=503)
    logger.info("Terminal actor=%s service=%s exit=%s", request.user.pk, app.pk, result["exit_code"])
    audit.record(
        request, "terminal.execute", target=app,
        detail={
            "task": task_id,
            "exit_code": result["exit_code"],
            "truncated": result["truncated"],
            "command_sha256": hashlib.sha256(command.encode()).hexdigest()[:16],
        },
    )
    return JsonResponse(result)


@never_cache
@login_required
@require_POST
def browse_files(request, pk):
    require_operator(request)
    if not settings.WEB_TERMINAL:
        raise PermissionDenied
    app = get_object_or_404(Application, pk=pk)
    task_id = request.POST.get("task", "")
    action = request.POST.get("action")
    if not 1 <= len(task_id) <= 64:
        return JsonResponse({"error": "Select a running replica."}, status=400)
    try:
        path = browse_path(request.POST.get("path", "/"))
        command = file_command(action, path)
    except BrowserError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    try:
        with operation_lock("container-files"):
            result = run_terminal(get_orchestrator(), app, task_id, command)
    except LockBusy:
        return JsonResponse({"error": "Another container operation is in progress."}, status=429)
    except (TerminalError, BrowserError) as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except (OrchestratorError, OSError):
        return JsonResponse({"error": "Could not contact the target Docker node."}, status=503)
    logger.info("File browser actor=%s service=%s operation=%s exit=%s",
                request.user.pk, app.pk, action, result["exit_code"])
    audit.record(
        request, "files.browse", target=app,
        detail={"task": task_id, "operation": action, "path": str(path),
                "exit_code": result["exit_code"]},
    )
    try:
        return JsonResponse(parse_file_response(action, path, result))
    except BrowserError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
