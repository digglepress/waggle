from io import StringIO
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.deployments.models import Application, Environment, Project
from orchestrator.types import ServiceInfo, TaskInfo

COMMAND = "apps.deployments.management.commands.exec_app"
CONTAINER = "a" * 64


class ExecAppTests(TestCase):
    def setUp(self):
        project = Project.objects.create(name="Demo", slug="demo")
        scope = Environment.objects.create(project=project, name="Production", slug="production")
        self.app = Application.objects.create(slug="demo", image="alpine:3", scope=scope,
                                              legacy_service_name=True)
        self.orchestrator = Mock()
        self.orchestrator.get_service.return_value = ServiceInfo(
            "abc", "waggle-app-demo", "alpine:3", "replicated", 1, 1,
            labels={"waggle.app-id": str(self.app.pk)},
        )
        self.orchestrator.service_tasks.return_value = [
            TaskInfo("task1", "abc", "node1", 1, "running", "running", container_id=CONTAINER),
            TaskInfo("task2", "abc", "node2", 1, "running", "shutdown", container_id="b" * 64),
        ]
        manager = patch(f"{COMMAND}.get_orchestrator", return_value=self.orchestrator)
        manager.start()
        self.addCleanup(manager.stop)

    def test_locate_skips_old_tasks_and_never_spawns_docker(self):
        output = StringIO()
        with patch(f"{COMMAND}.subprocess.run") as run:
            call_command("exec_app", "--locate", "demo", stdout=output)
        self.assertEqual(output.getvalue(), f"task1\tnode1\t{CONTAINER}\n")
        run.assert_not_called()

    def test_passes_command_arguments_through_to_local_container(self):
        with (
            patch(f"{COMMAND}.sys.stdin.isatty", return_value=False),
            patch(f"{COMMAND}.subprocess.run", side_effect=[
                CompletedProcess([], 0, stdout="node1\n"), CompletedProcess([], 0)
            ]) as run,
        ):
            call_command("exec_app", "--task", "task1", "demo", "--", "ls", "-l")
        self.assertEqual(run.call_args.args[0], ["docker", "exec", "-i", CONTAINER, "ls", "-l"])

    def test_refuses_to_exec_on_a_different_node(self):
        with patch(f"{COMMAND}.subprocess.run",
                   return_value=CompletedProcess([], 0, stdout="node3\n")) as run:
            with self.assertRaisesMessage(CommandError, "No matching task on this Docker node"):
                call_command("exec_app", "demo")
        run.assert_called_once()
