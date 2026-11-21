from __future__ import annotations

from django.test import SimpleTestCase

from orchestrator.errors import OrchestratorError, OrchestratorUnavailable, ServiceNotFound
from orchestrator.fake import FakeOrchestrator
from orchestrator.spec import ServiceSpec


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
