import logging

from django.db import close_old_connections
from django.utils import timezone

from apps.core import notify
from apps.core.logs import deployment_log_path
from apps.deployments.build_logs import deployment_line
from apps.deployments.models import Deployment
from apps.deployments.services import DeploymentError, deploy, rollback
from config.huey import huey
from orchestrator.errors import OrchestratorError

logger = logging.getLogger("waggle.deployments")


@huey.task()
def deploy_application(pk):
    close_old_connections()
    try:
        _run(pk)
    finally:
        close_old_connections()


def _run(pk):
    state = Deployment.State
    if not Deployment.objects.filter(pk=pk, state=state.QUEUED).update(state=state.RUNNING):
        return
    job = Deployment.objects.select_related("application").get(pk=pk)
    path = deployment_log_path(pk)

    def write_line(message):
        try:
            deployment_line(path, message)
        except OSError:
            logger.warning("Could not write deployment log %s", pk)

    def mark_converging():
        job.state = state.CONVERGING
        Deployment.objects.filter(pk=pk).update(state=state.CONVERGING, updated_at=timezone.now())
        write_line("Waiting for the new tasks to become healthy.")

    def log_progress(progress):
        write_line(progress.message)

    try:
        deployment_line(path, f"Starting {job.get_operation_display().lower()} for {job.application.slug}.")
        if job.operation == Deployment.Operation.ROLLBACK and not job.image:
            rollback(job.application, on_progress=log_progress)
            job.state = state.SUBMITTED
        else:
            job.image = deploy(
                job, resolved_image=job.image or None,
                on_converge=mark_converging, on_progress=log_progress,
            )
            job.state = state.SUBMITTED
    except (DeploymentError, OrchestratorError) as exc:
        job.state, job.error = state.FAILED, str(exc)[:2000]
    except Exception:
        logger.exception("Deployment %s failed unexpectedly", pk)
        job.state, job.error = state.FAILED, "Unexpected failure. Check the worker logs."
    try:
        if job.state == state.SUBMITTED:
            deployment_line(path, "Deployment converged and is running.")
        else:
            deployment_line(path, job.error or "Deployment failed.")
    except OSError:
        logger.warning("Could not write deployment log %s", pk)
    job.save(update_fields=["state", "image", "error", "updated_at"])
    notify.deployment_finished(job)
