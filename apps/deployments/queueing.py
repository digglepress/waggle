"""Create and enqueue deployments from the web UI and the API."""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from apps.deployments.models import Deployment
from apps.deployments.services import snapshot
from apps.deployments.tasks import deploy_application

logger = logging.getLogger("waggle.deployments")


class DeploymentConflict(RuntimeError):
    """The service already has queued or running deployment."""


class DeploymentQueueError(RuntimeError):
    """The job was created but the worker could not be reached."""


def queue_deployment(app, operation, *, target=None) -> Deployment:
    try:
        with transaction.atomic():
            job = Deployment.objects.create(
                application=app,
                operation=operation,
                spec=target.spec if target else snapshot(app),
                rollback_target=target,
                image=target.image if target else "",
            )
    except IntegrityError as exc:
        raise DeploymentConflict from exc
    try:
        deploy_application(job.pk)
    except Exception as exc:  # noqa: BLE001 - surface a clean API/UI error
        logger.exception("Could not enqueue deployment %s", job.pk)
        Deployment.objects.filter(pk=job.pk, state=Deployment.State.QUEUED).update(
            state=Deployment.State.FAILED,
            error="Could not queue deployment. Check the worker logs.",
        )
        raise DeploymentQueueError from exc
    return job
