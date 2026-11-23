import base64
import shlex
from unittest.mock import MagicMock, Mock, patch

import paramiko
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.deployments.models import Application, Environment, Project
from apps.deployments.terminal import (
    OUTPUT_LIMIT,
    Output,
    PinnedHostKey,
    SSHOptions,
    TerminalError,
    _run_ssh,
    run_terminal,
)
from orchestrator.types import ServiceInfo, SwarmInfo, TaskInfo


@override_settings(WEB_TERMINAL=True)
class TerminalViewTests(TestCase):
    def setUp(self):
        project = Project.objects.create(name="Demo", slug="demo")
        scope = Environment.objects.create(name="Production", slug="prod", project=project)
        self.app = Application.objects.create(scope=scope, slug="api", image="alpine:3")
        users = get_user_model().objects
        self.operator = users.create_user("operator", is_staff=True)
        self.viewer = users.create_user("viewer")
        self.url = reverse("deployments:terminal_execute", args=[self.app.pk])

    def test_operator_executes_with_bounded_input(self):
        self.client.force_login(self.operator)
        with patch("apps.deployments.terminal_views.run_terminal", return_value={
            "output": "<sample>\n", "exit_code": 0, "truncated": False,
        }) as run:
            reply = self.client.post(self.url, {"task": "task-1", "command": "pwd"})
            self.assertEqual(reply.json()["output"], "<sample>\n")
            self.assertIn("no-store", reply["Cache-Control"])
            self.assertEqual(run.call_args.args[-2:], ("task-1", "pwd"))
            too_long = self.client.post(self.url, {"task": "task-1", "command": "x" * 4097})
            self.assertEqual(too_long.status_code, 400)
            run.assert_called_once()

    def test_viewer_and_anonymous_cannot_execute(self):
        with patch("apps.deployments.terminal_views.run_terminal") as run:
            self.assertEqual(self.client.post(self.url, {}).status_code, 302)
            self.client.force_login(self.viewer)
            self.assertEqual(self.client.post(self.url, {}).status_code, 403)
            run.assert_not_called()

    def test_command_requires_post_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.get(self.url).status_code, 405)
        self.assertEqual(client.post(self.url, {"task": "task-1", "command": "pwd"}).status_code, 403)

    @override_settings(WEB_TERMINAL=False)
    def test_terminal_is_opt_in(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)
        page = self.client.get(reverse("deployments:terminal", args=[self.app.pk]))
        self.assertContains(page, "browser terminal is disabled")


class TerminalRunnerTests(SimpleTestCase):
    def setUp(self):
        self.orchestrator = Mock()
        self.app = Mock(pk=1, slug="demo", legacy_service_name=False)
        self.orchestrator.get_service.return_value = ServiceInfo(
            "service-id", "waggle-service-1-demo", "alpine", "replicated", 1, 1,
            labels={"waggle.app-id": "1"},
        )
        self.orchestrator.swarm_info.return_value = SwarmInfo(
            node_id="manager", cluster_id="cluster", control_available=True, node_addr="192.0.2.1",
            local_state="active",
        )
        self.orchestrator.service_tasks.return_value = [
            TaskInfo("task-1", "service-id", "worker", 1, "running", "running", container_id="a" * 64),
            TaskInfo("task-2", "other-service", "worker", 1, "running", "running", container_id="b" * 64),
        ]

    def test_foreign_or_stale_task_never_gets_a_transport(self):
        with patch("apps.deployments.terminal.ssh_options") as credentials, self.assertRaises(TerminalError):
            run_terminal(self.orchestrator, self.app, "task-2", "pwd")
        credentials.assert_not_called()

    def test_uses_exact_worker_node_and_cluster(self):
        ssh = SSHOptions("worker.example", 2222, "root", "pin", "key", restricted=True)
        with (
            patch("apps.deployments.terminal.ssh_options", return_value=ssh) as credentials,
            patch("apps.deployments.terminal._run_ssh") as transport,
        ):
            run_terminal(self.orchestrator, self.app, "task-1", "printf 'test; quote'")
        credentials.assert_called_once_with("worker", "cluster")
        transport.assert_called_once_with(ssh, [
            "worker", "service-id", "task-1", "a" * 64, "printf 'test; quote'",
        ])

    def test_restricted_protocol_serializes_the_command_as_data(self):
        client = Mock()
        client.get_transport.return_value.open_session.return_value = MagicMock()
        channel = client.get_transport.return_value.open_session.return_value.__enter__.return_value
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        args = ["worker", "service", "task", "a" * 64, "echo '$HOME'; pwd"]
        with (
            patch("apps.deployments.terminal.paramiko.SSHClient", return_value=client),
            patch("apps.deployments.terminal.load_private_key"),
        ):
            self.assertEqual(_run_ssh(SSHOptions("worker", 2222, "root", "pin", "key", True), args)["exit_code"], 0)
        argv = shlex.split(channel.exec_command.call_args.args[0])
        self.assertEqual(argv[:5], ["waggle-terminal", *args[:4]])
        self.assertEqual(base64.b64decode(argv[5]).decode(), args[-1])
        self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0], PinnedHostKey)

    def test_host_key_mismatch_is_rejected(self):
        with patch("apps.deployments.terminal.fingerprint", return_value="wrong"):
            with self.assertRaises(paramiko.SSHException):
                PinnedHostKey("expected").missing_host_key(None, "worker", None)

    def test_output_is_bounded(self):
        output = Output()
        output.append(b"x" * (OUTPUT_LIMIT - 1))
        output.append(b"ab")
        self.assertEqual(len(output.result(0)["output"]), OUTPUT_LIMIT)
        self.assertTrue(output.result(0)["truncated"])
