from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.logs import append_line
from apps.core.orchestration import reset_orchestrator_cache
from apps.servers.models import Server, SSHKey
from apps.servers.services import (
    refresh_server,
    remove_server,
    run_bootstrap_for,
    set_availability,
    sync_local_node,
)
from orchestrator.errors import OrchestratorError
from orchestrator.fake import FakeOrchestrator
from provisioning.bootstrap import BootstrapError
from tests.test_bootstrap import base_executor


@override_settings(ORCHESTRATOR="orchestrator.fake:FakeOrchestrator")
class WaggleTestCase(TestCase):
    def setUp(self):
        reset_orchestrator_cache()
        FakeOrchestrator.reset()
        self.orch = FakeOrchestrator()
        self.orch.reset_state()

        self.user = User.objects.create_user("admin", password="secret", is_staff=True)
        self.client.force_login(self.user)
        self.key = SSHKey.objects.create(
            name="test-key",
            private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n",
        )

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_override = override_settings(LOG_DIR=Path(self.tmp.name))
        self.log_override.enable()
        self.addCleanup(self.log_override.disable)

    def make_server(self, **kwargs) -> Server:
        defaults = {
            "name": "edge-1",
            "address": "10.0.0.2",
            "ssh_key": self.key,
            "role": Server.Role.WORKER,
        }
        defaults.update(kwargs)
        return Server.objects.create(**defaults)


class AuthAndPageTests(WaggleTestCase):
    def test_dashboard_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_dashboard_renders(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")

    def test_dashboard_counts_only_running_services(self):
        from apps.core.orchestration import ClusterStatus
        from orchestrator.types import ServiceInfo

        services = [
            ServiceInfo("1", "one", "img", "replicated", 2, 2),
            ServiceInfo("2", "two", "img", "replicated", 1, 0),
        ]
        cluster = ClusterStatus(ok=True, services=services)
        with mock.patch("apps.core.views.cluster_status", return_value=cluster):
            response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.content.decode(),
            r"SERVICES ACTIVE</span>\s*<span class=\"stat-value\">1</span>",
        )

    def test_cluster_page_renders_without_a_swarm(self):
        response = self.client.get(reverse("core:cluster"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not part of a swarm")

    def test_health_is_public(self):
        self.client.logout()
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_login_page_renders(self):
        self.client.logout()
        response = self.client.get("/login/")
        self.assertContains(response, "Sign in")


class ServerFormTests(WaggleTestCase):
    def test_add_server_creates_and_enqueues_bootstrap(self):
        payload = {
            "name": "edge-1",
            "address": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key": self.key.pk,
            "role": "worker",
            "advertise_addr": "",
            "labels": "{}",
        }
        with mock.patch("apps.servers.views.bootstrap_server") as enqueue:
            response = self.client.post(reverse("servers:add"), payload)

        server = Server.objects.get(name="edge-1")
        self.assertRedirects(response, server.get_absolute_url())
        self.assertEqual(server.status, Server.Status.PROVISIONING)
        enqueue.assert_called_once_with(server.pk)

    def test_labels_are_parsed_from_json(self):
        payload = {
            "name": "edge-2",
            "address": "203.0.113.11",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key": self.key.pk,
            "role": "worker",
            "advertise_addr": "",
            "labels": '{"zone": "eu-west"}',
        }
        with mock.patch("apps.servers.views.bootstrap_server"):
            self.client.post(reverse("servers:add"), payload)
        self.assertEqual(Server.objects.get(name="edge-2").labels, {"zone": "eu-west"})

    def test_reserved_label_prefix_is_rejected(self):
        payload = {
            "name": "edge-3",
            "address": "203.0.113.12",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key": self.key.pk,
            "role": "worker",
            "advertise_addr": "",
            "labels": '{"waggle.managed": "false"}',
        }
        response = self.client.post(reverse("servers:add"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Server.objects.filter(name="edge-3").exists())

    def test_ssh_key_is_required(self):
        payload = {
            "name": "edge-4",
            "address": "203.0.113.13",
            "ssh_port": 22,
            "ssh_user": "root",
            "role": "worker",
            "advertise_addr": "",
            "labels": "{}",
        }
        response = self.client.post(reverse("servers:add"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Server.objects.filter(name="edge-4").exists())


class ServerServiceTests(WaggleTestCase):
    def test_refresh_server_maps_node_state(self):
        server = self.make_server()
        self.orch.add_node(
            "edge-1",
            address="10.0.0.2",
            labels={"waggle.managed": "true", "zone": "eu"},
            cpu_cores=8,
            memory_mb=8192,
        )
        refresh_server(server)
        server.refresh_from_db()
        self.assertEqual(server.status, Server.Status.READY)
        self.assertEqual(server.role, Server.Role.WORKER)
        self.assertEqual(server.labels["zone"], "eu")
        self.assertEqual(server.cpu_cores, 8)
        self.assertEqual(server.ram_mb, 8192)
        self.assertIsNotNone(server.last_seen)

    def test_refresh_marks_missing_node_offline(self):
        server = self.make_server()
        refresh_server(server)
        server.refresh_from_db()
        self.assertEqual(server.status, Server.Status.OFFLINE)
        self.assertIn("not registered", server.last_error)

    def test_drain_updates_the_orchestrator(self):
        server = self.make_server(swarm_node_id="node_x", status=Server.Status.READY)
        self.orch.add_node("edge-1", node_id="node_x")
        set_availability(server, Server.Availability.DRAIN)
        server.refresh_from_db()
        self.assertEqual(server.availability, Server.Availability.DRAIN)
        self.assertEqual(server.status, Server.Status.DRAINING)
        self.assertEqual(self.orch.nodes["node_x"].availability, "drain")

    def test_local_server_cannot_be_removed(self):
        server = self.make_server(is_local=True, name="control", ssh_key=None)
        with self.assertRaises(OrchestratorError):
            remove_server(server)
        self.assertTrue(Server.objects.filter(pk=server.pk).exists())

    def test_remove_remote_server_drains_then_deletes(self):
        server = self.make_server(swarm_node_id="node_x")
        self.orch.add_node("edge-1", node_id="node_x")
        remove_server(server)
        self.assertFalse(Server.objects.filter(name="edge-1").exists())
        self.assertNotIn("node_x", self.orch.nodes)
        self.assertIn("update_node", self.orch.call_names())

    def test_sync_local_node_registers_the_control_plane(self):
        node = self.orch.add_node("manager-1", role="manager", address="10.0.0.1")
        self.orch.set_swarm()
        server = sync_local_node()
        self.assertIsNotNone(server)
        self.assertTrue(server.is_local)
        self.assertEqual(server.role, Server.Role.MANAGER)
        self.assertEqual(server.swarm_node_id, node.id)
        self.assertEqual(server.status, Server.Status.READY)

    def test_sync_local_node_is_idempotent(self):
        self.orch.add_node("manager-1", role="manager", address="10.0.0.1")
        self.orch.set_swarm()
        sync_local_node()
        sync_local_node()
        self.assertEqual(Server.objects.filter(is_local=True).count(), 1)

    def test_sync_local_node_returns_none_without_a_swarm(self):
        self.assertIsNone(sync_local_node())


class BootstrapTaskTests(WaggleTestCase):
    def test_run_bootstrap_for_persists_progress(self):
        server = self.make_server()
        self.orch.add_node("manager-1", role="manager", address="10.0.0.1")
        self.orch.set_swarm()
        self.orch.add_node("edge-1", address="10.0.0.2")

        with mock.patch(
            "apps.servers.services.build_executor", return_value=base_executor()
        ):
            run_bootstrap_for(server)

        server.refresh_from_db()
        self.assertEqual(server.status, Server.Status.READY)
        self.assertEqual(server.bootstrap_step, "labels")
        self.assertTrue(server.swarm_node_id)
        self.assertEqual(server.docker_version, "27.5.1")
        self.assertEqual(server.bootstrap_error, "")

    def test_missing_ssh_key_marks_the_server_failed(self):
        server = self.make_server(ssh_key=None)
        with self.assertRaises(ValueError):
            run_bootstrap_for(server)
        server.refresh_from_db()
        self.assertEqual(server.status, Server.Status.ERROR)
        self.assertIn("no SSH key", server.bootstrap_error)

    def test_run_bootstrap_for_records_failure(self):
        server = self.make_server()
        self.orch.add_node("manager-1", role="manager")
        self.orch.set_swarm()

        with (
            mock.patch(
                "apps.servers.services.build_executor",
                return_value=base_executor(memory="Mem: 200 100 50\n"),
            ),
            self.assertRaises(BootstrapError),
        ):
            run_bootstrap_for(server)

        server.refresh_from_db()
        self.assertEqual(server.status, Server.Status.ERROR)
        self.assertIn("preflight", server.bootstrap_error)

    def test_action_bootstrap_queues_a_task(self):
        server = self.make_server(status=Server.Status.ERROR)
        with mock.patch("apps.servers.views.bootstrap_server") as enqueue:
            response = self.client.post(
                reverse("servers:action", args=[server.pk]),
                {"action": "bootstrap"},
            )
        self.assertEqual(response.status_code, 302)
        enqueue.assert_called_once_with(server.pk)
        server.refresh_from_db()
        self.assertEqual(server.status, Server.Status.PROVISIONING)

    def test_local_server_cannot_be_bootstrapped(self):
        server = self.make_server(is_local=True, name="control", ssh_key=None)
        response = self.client.post(
            reverse("servers:action", args=[server.pk]),
            {"action": "bootstrap"},
            follow=True,
        )
        self.assertContains(response, "install.sh")
        server.refresh_from_db()
        self.assertNotEqual(server.status, Server.Status.PROVISIONING)


class LogStreamTests(WaggleTestCase):
    def test_stream_replays_log_then_finishes(self):
        server = self.make_server(status=Server.Status.READY)
        append_line(Path(self.tmp.name) / f"server-{server.pk}.log", "hello from the worker")

        response = self.client.get(reverse("servers:log", args=[server.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        body = b"".join(response.streaming_content).decode()
        self.assertIn("hello from the worker", body)
        self.assertIn("event: done", body)
