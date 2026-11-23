import base64
import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from docker.auth import get_config_header

from orchestrator.spec import RegistryAuth, ServiceSpec
from orchestrator.swarm import SwarmOrchestrator


class SwarmSDKTests(SimpleTestCase):
    def setUp(self):
        self.orchestrator = SwarmOrchestrator()
        self.client = Mock()
        self.orchestrator._client = self.client
        self.service = self.client.services.get.return_value
        self.service.id = "service-id"
        self.service.attrs = {
            "ID": "service-id",
            "Version": {"Index": 42},
            "Spec": {"Name": "existing-name", "Mode": {"Replicated": {"Replicas": 2}}},
        }

    def test_update_keeps_existing_name(self):
        self.orchestrator.update_service("service-id", ServiceSpec(name="app", image="app:1"))
        args, kwargs = self.client.api.update_service.call_args
        self.assertEqual(args, ("service-id",))
        self.assertEqual(kwargs["name"], "existing-name")
        self.assertEqual(kwargs["version"], 42)

    def test_registry_credentials_stay_on_an_isolated_client(self):
        isolated = Mock()
        isolated.api.create_service.return_value = {"ID": "new-id"}
        spec = ServiceSpec(
            name="app", image="ghcr.io/example/app:1",
            registry_auth=RegistryAuth("ghcr.io", "operator", "private-token"),
        )
        with patch("orchestrator.swarm.docker.from_env", return_value=isolated):
            self.assertEqual(self.orchestrator.create_service(spec), "new-id")
        header = get_config_header(isolated.api, "ghcr.io")
        self.assertEqual(json.loads(base64.urlsafe_b64decode(header))["password"], "private-token")
        self.assertIsNone(get_config_header(isolated.api, "other.example.org"))
        self.client.api.create_service.assert_not_called()
        isolated.close.assert_called_once()

    def test_get_requests_task_status(self):
        attrs = {**self.service.attrs, "ServiceStatus": {"RunningTasks": 1, "DesiredTasks": 0}}
        self.client.services.list.return_value = [Mock(attrs=attrs)]
        service = self.orchestrator.get_service("existing-name")
        self.client.services.list.assert_called_once_with(
            filters={"id": "service-id"}, status=True
        )
        self.assertEqual(service.replicas_desired, 2)
        self.assertEqual(service.replicas_running, 1)

    def test_rollback_sends_a_full_spec_and_checks_response(self):
        self.orchestrator.rollback_service("service-id")
        self.client.api._url.assert_called_once_with("/services/service-id/update")
        self.client.api._post_json.assert_called_once_with(
            self.client.api._url.return_value,
            data=self.service.attrs["Spec"],
            params={"version": 42, "rollback": "previous"},
        )
        self.client.api._raise_for_status.assert_called_once_with(
            self.client.api._post_json.return_value
        )

    def test_node_update_uses_full_spec(self):
        node = self.client.nodes.get.return_value
        node.attrs = {"Spec": {"Role": "manager", "Availability": "active", "Labels": {"a": "b"}}}
        self.orchestrator.update_node("node-id", availability="drain")
        node.update.assert_called_once_with(
            {"Role": "manager", "Availability": "drain", "Labels": {"a": "b"}}
        )

    def test_join_tokens_are_read_from_engine(self):
        self.client.api.inspect_swarm.return_value = {
            "JoinTokens": {"Worker": "worker-token", "Manager": "manager-token"}
        }
        self.assertEqual(
            self.orchestrator.join_tokens(),
            {"Worker": "worker-token", "Manager": "manager-token"},
        )
