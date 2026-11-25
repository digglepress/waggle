from __future__ import annotations

from dataclasses import replace

from django.test import SimpleTestCase

from orchestrator.errors import OrchestratorError, OrchestratorUnavailable, ServiceNotFound
from orchestrator.fake import FakeOrchestrator
from orchestrator.spec import ServiceSpec
from orchestrator.types import TaskInfo


class PullingOrchestrator(FakeOrchestrator):
    """Keep tasks in ``preparing`` for a few polls to simulate a slow pull."""

    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self, *, ready_after=8):
        super().__init__()
        self.polls = 0
        self.ready_after = ready_after

    def service_tasks(self, service_id):
        tasks = super().service_tasks(service_id)
        self.polls += 1
        state = "running" if self.polls >= self.ready_after else "preparing"
        return [
            replace(task, state=state, message="" if state == "running" else "preparing")
            for task in tasks
        ]


class UpdatingOrchestrator(FakeOrchestrator):
    """An old task is still running while the replacement task prepares."""

    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self):
        super().__init__()
        self.polls = 0

    def service_tasks(self, service_id):
        self.polls += 1
        state = "running" if self.polls >= 5 else "preparing"
        return [
            TaskInfo("old", service_id, "node_1", 1, "running", "shutdown"),
            TaskInfo("new", service_id, "node_1", 1, state, "running"),
        ]


class StartingAfterPullOrchestrator(FakeOrchestrator):
    """The pull finishes and the container spends time in ``starting``."""

    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self):
        super().__init__()
        self.polls = 0

    def service_tasks(self, service_id):
        tasks = super().service_tasks(service_id)
        self.polls += 1
        if self.polls < 8:
            state = "preparing"
        elif self.polls < 11:
            state = "starting"
        else:
            state = "running"
        return [
            replace(task, state=state, message="" if state == "running" else state)
            for task in tasks
        ]


class FakeOrchestratorTests(SimpleTestCase):
    def setUp(self):
        FakeOrchestrator.reset()
        self.orch = FakeOrchestrator()
        self.orch.reset_state()

    def spec(self, name="app", replicas=2):
        return ServiceSpec(name=name, image=f"{name}:1", replicas=replicas)

    def test_singleton_shares_state(self):
        self.assertIs(FakeOrchestrator(), FakeOrchestrator())

    def test_network_is_idempotent(self):
        first = self.orch.ensure_network("paas_proxy")
        second = self.orch.ensure_network("paas_proxy")
        self.assertEqual(first, second)
        self.assertEqual(len(self.orch.list_networks()), 1)

    def test_service_lifecycle(self):
        service_id = self.orch.create_service(self.spec())
        info = self.orch.get_service("app")
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "app")
        self.assertEqual(info.replicas_desired, 2)
        self.assertEqual(info.replicas_running, 2)

        self.orch.update_service("app", self.spec(replicas=3))
        self.assertEqual(self.orch.get_service(service_id).replicas_desired, 3)

        self.orch.remove_service(service_id)
        self.assertIsNone(self.orch.get_service(service_id))

    def test_duplicate_service_is_rejected(self):
        self.orch.create_service(self.spec())
        with self.assertRaises(OrchestratorError):
            self.orch.create_service(self.spec())

    def test_missing_service_raises(self):
        with self.assertRaises(ServiceNotFound):
            self.orch.remove_service("nope")

    def test_convergence_success(self):
        service_id = self.orch.create_service(self.spec())
        result = self.orch.wait_for_convergence(service_id, timeout=0.2, poll_interval=0.01)
        self.assertTrue(result.converged)
        self.assertEqual(result.running, 2)

    def test_convergence_times_out_when_tasks_are_pending(self):
        service_id = self.orch.create_service(self.spec())
        self.orch.set_running("app", 0)
        result = self.orch.wait_for_convergence(service_id, timeout=0.15, poll_interval=0.02)
        self.assertFalse(result.converged)
        self.assertEqual(result.running, 0)
        self.assertIn("0/2", result.message)

    def test_a_slow_pull_extends_the_deadline_and_reports_progress(self):
        orch = PullingOrchestrator(ready_after=8)
        service_id = orch.create_service(ServiceSpec(name="app", image="app:1", replicas=2))
        orch.set_running("app", 0)
        progress = []
        result = orch.wait_for_convergence(
            service_id, timeout=0.1, pull_timeout=5, poll_interval=0.02,
            progress_interval=0.01, on_progress=progress.append,
        )
        self.assertTrue(result.converged)
        self.assertGreaterEqual(result.elapsed_s, 0.1)
        self.assertTrue(any("image pull" in item.message.lower() for item in progress))
        self.assertTrue(any("still in progress after" in item.message for item in progress))

    def test_a_slow_pull_then_start_gets_a_fresh_startup_window(self):
        orch = StartingAfterPullOrchestrator()
        service_id = orch.create_service(ServiceSpec(name="app", image="app:1", replicas=2))
        orch.set_running("app", 0)
        progress = []
        result = orch.wait_for_convergence(
            service_id, timeout=0.1, pull_timeout=5, poll_interval=0.02,
            progress_interval=0.01, on_progress=progress.append,
        )
        self.assertTrue(result.converged)
        self.assertGreaterEqual(result.elapsed_s, 0.2)
        self.assertTrue(any("pull finished" in item.message for item in progress))

    def test_the_pull_timeout_still_fails_with_a_clear_message(self):
        orch = PullingOrchestrator(ready_after=999)
        service_id = orch.create_service(ServiceSpec(name="app", image="app:1", replicas=2))
        orch.set_running("app", 0)
        result = orch.wait_for_convergence(
            service_id, timeout=0.05, pull_timeout=0.15, poll_interval=0.02,
        )
        self.assertFalse(result.converged)
        self.assertTrue(result.pulling)
        self.assertIn("image pull still in progress", result.message)
        self.assertIn("0/2", result.message)

    def test_update_ignores_tasks_that_are_being_shut_down(self):
        orch = UpdatingOrchestrator()
        service_id = orch.create_service(ServiceSpec(name="app", image="app:1", replicas=1))
        result = orch.wait_for_convergence(
            service_id, timeout=0.1, pull_timeout=1, poll_interval=0.02,
            progress_interval=0.01,
        )
        self.assertTrue(result.converged)
        self.assertGreaterEqual(result.elapsed_s, 0.08)

    def test_global_mode_counts_ready_nodes(self):
        self.orch.add_node("a", role="manager")
        self.orch.add_node("b")
        self.orch.add_node("c", state="down")
        spec = ServiceSpec(name="edge", image="traefik", global_mode=True)
        self.orch.create_service(spec)
        self.assertEqual(self.orch.get_service("edge").replicas_desired, 2)

    def test_nodes(self):
        node = self.orch.add_node("edge-1", role="manager", labels={"zone": "eu"})
        self.orch.set_swarm()
        self.assertTrue(self.orch.swarm_info().control_available)

        self.orch.update_node(node.id, availability="drain", labels={"zone": "eu", "tier": "edge"})
        updated = self.orch.list_nodes()[0]
        self.assertEqual(updated.availability, "drain")
        self.assertEqual(updated.labels["tier"], "edge")

        tokens = self.orch.join_tokens()
        self.assertIn("Worker", tokens)
        self.orch.remove_node(node.id)
        self.assertEqual(self.orch.list_nodes(), [])

    def test_secrets_configs_and_volumes(self):
        self.orch.create_secret("pw", b"hunter2")
        self.assertEqual(self.orch.secrets["pw"], b"hunter2")
        self.orch.remove_secret("pw")
        self.assertNotIn("pw", self.orch.secrets)

        self.orch.create_volume("data")
        self.assertIn("data", self.orch.volumes)

    def test_unavailable_backend_raises(self):
        self.orch.unavailable = True
        with self.assertRaises(OrchestratorUnavailable):
            self.orch.ping()

    def test_calls_are_recorded(self):
        self.orch.ensure_network("paas_proxy")
        self.orch.list_nodes()
        self.assertEqual(self.orch.call_names(), ["ensure_network", "list_nodes"])
