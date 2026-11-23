from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.orchestration import reset_orchestrator_cache
from apps.servers.models import Server
from orchestrator.fake import FakeOrchestrator


@override_settings(ORCHESTRATOR="orchestrator.fake:FakeOrchestrator")
class AdoptWorkerTests(TestCase):
    def setUp(self):
        self.addCleanup(reset_orchestrator_cache)
        self.addCleanup(FakeOrchestrator.reset)
        reset_orchestrator_cache()
        FakeOrchestrator.reset()
        self.orchestrator = FakeOrchestrator()
        self.manager = self.orchestrator.add_node("manager", role="manager")
        self.orchestrator.set_swarm()
        self.worker = self.orchestrator.add_node("worker-1", address="192.0.2.2")

    def test_explicitly_joins_database_without_an_ssh_key(self):
        call_command("adopt_workers", self.worker.id)
        call_command("adopt_workers", self.worker.id)
        row = Server.objects.get(swarm_node_id=self.worker.id)
        self.assertEqual(row.status, Server.Status.READY)
        self.assertEqual(row.role, Server.Role.WORKER)
        self.assertEqual(row.address, self.worker.address)
        self.assertFalse(row.can_bootstrap)
        row.full_clean()
        self.assertTrue(reverse("servers:detail", args=[row.pk]))

    def test_does_not_adopt_manager_or_partially_apply_request(self):
        with self.assertRaisesMessage(CommandError, "is not a worker"):
            call_command("adopt_workers", self.worker.id, self.manager.id)
        self.assertFalse(Server.objects.exists())
