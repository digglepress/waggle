"""Open a local task's container without accepting shell access over HTTP."""

import argparse
import re
import subprocess
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.core.orchestration import get_orchestrator
from apps.deployments.models import Application
from apps.deployments.services import owned_service
from orchestrator.errors import OrchestratorError


class Command(BaseCommand):
    help = "Locate app tasks, or exec into a running container on the local Docker node."

    def add_arguments(self, parser):
        parser.add_argument("--locate", action="store_true", help="List task, node and container IDs.")
        parser.add_argument("--task", help="Task ID or unique prefix, supplied before the app slug.")
        parser.add_argument("--context", help="Local Docker CLI context for the selected task's node.")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--files", metavar="DIRECTORY", help="List files in a container directory.")
        mode.add_argument("--read", metavar="FILE", help="Read up to 64 KiB from a container file.")
        parser.add_argument("app", help="Service slug or project/environment/service.")
        parser.add_argument("argv", nargs=argparse.REMAINDER)

    def handle(self, *args, **options):
        path = options["app"].split("/")
        try:
            if len(path) == 3:
                app = Application.objects.get(scope__project__slug=path[0], scope__slug=path[1], slug=path[2])
            else:
                app = Application.objects.get(slug=options["app"])
        except Application.DoesNotExist:
            raise CommandError("No service with that path.") from None
        except Application.MultipleObjectsReturned:
            raise CommandError("Use project/environment/service to identify this service.") from None
        try:
            orchestrator = get_orchestrator()
            service = owned_service(orchestrator, app)
            if service is None:
                raise CommandError("The application has no Swarm service.")
            candidates = [
                task for task in orchestrator.service_tasks(service.id)
                if task.is_running and task.desired_state == "running"
                and re.fullmatch(r"[0-9a-f]{64}", task.container_id)
                and (not options["task"] or task.id.startswith(options["task"]))
            ]
        except OrchestratorError as exc:
            raise CommandError(str(exc)) from exc
        if not candidates:
            raise CommandError("No matching running tasks with container IDs.")
        if options["locate"]:
            for task in candidates:
                self.stdout.write(f"{task.id}\t{task.node_id}\t{task.container_id}")
            return

        if options["task"] and len(candidates) != 1:
            raise CommandError("Task ID prefix is ambiguous. Run with --locate.")
        docker = ["docker"]
        if options["context"]:
            docker.extend(["--context", options["context"]])
        local_node = self._docker_node(docker)
        local_tasks = sorted(
            (task for task in candidates if task.node_id == local_node),
            key=lambda task: (task.slot, task.id),
        )
        if not local_tasks:
            raise CommandError(
                "No matching task on this Docker node. Use --locate, then run docker exec on "
                "the task's host."
            )
        flags = []
        argv = options["argv"]
        if argv and argv[0] == "--":
            argv = argv[1:]
        if options["files"] or options["read"]:
            path = options["files"] or options["read"]
            if not path.startswith("/") or argv:
                raise CommandError("File browsing requires an absolute path and no command arguments.")
            argv = (["ls", "-la", "--", path] if options["files"]
                    else ["head", "-c", "65536", "--", path])
        else:
            flags.append("-i")
            if sys.stdin.isatty() and sys.stdout.isatty():
                flags.append("-t")
        try:
            result = subprocess.run(
                [*docker, "exec", *flags, local_tasks[0].container_id, *(argv or ["/bin/sh"])],
                check=False,
            )
        except OSError as exc:
            raise CommandError(f"Could not start Docker CLI: {exc}") from exc
        if result.returncode:
            raise CommandError("docker exec did not complete successfully.",
                               returncode=max(1, result.returncode))

    @staticmethod
    def _docker_node(docker):
        try:
            result = subprocess.run(
                [*docker, "info", "--format", "{{.Swarm.NodeID}}"],
                capture_output=True, text=True, check=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CommandError("Could not identify the Docker CLI's local Swarm node.") from exc
        node_id = result.stdout.strip()
        if not re.fullmatch(r"[0-9a-z]+", node_id):
            raise CommandError("Docker CLI is not connected to an active Swarm node.")
        return node_id
