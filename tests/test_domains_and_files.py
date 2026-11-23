import base64
import shlex
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.deployments.container_files import (
    PREVIEW_LIMIT,
    BrowserError,
    browse_path,
    file_command,
    parse_file_response,
)
from apps.deployments.domains import DomainError, validate_routing
from apps.deployments.models import Application, Deployment, Environment, Project
from apps.deployments.services import DeploymentError, make_spec, rollback, snapshot
from orchestrator.types import ServiceInfo


def result(output, *, truncated=False):
    return {"output": output, "exit_code": 0, "truncated": truncated}


class ReplicaFileTests(SimpleTestCase):
    def test_directory_records_preserve_names_and_dont_follow_links(self):
        path = browse_path("/app")
        entries = parse_file_response("list", path, result(
            "file\0space ' quote.txt\0link\0link with spaces\0directory\0assets\0",
        ))["entries"]
        self.assertEqual([entry["kind"] for entry in entries], ["directory", "link", "file"])
        self.assertEqual(entries[2]["path"], "/app/space ' quote.txt")

    def test_quoting_keeps_paths_outside_the_script(self):
        raw = "/app/name'; echo not-a-command"
        argv = shlex.split(file_command("preview", browse_path(raw)))
        self.assertEqual(argv[-1], raw)
        self.assertNotIn("not-a-command", argv[2])
        for value in ("../../outside", "/app/../other", "/app/\0name", "//app"):
            with self.assertRaises(BrowserError):
                browse_path(value)

    def test_preview_detects_binary_and_caps_utf8(self):
        path = browse_path("/app/config")
        data = b"a" * (PREVIEW_LIMIT - 1) + "é".encode()
        view = parse_file_response("preview", path, result(base64.b64encode(data).decode()))
        self.assertTrue(view["truncated"])
        self.assertFalse(view["binary"])
        self.assertEqual(len(view["text"]), PREVIEW_LIMIT - 1)
        view = parse_file_response("preview", path, result("AAEC"))
        self.assertTrue(view["binary"])
        self.assertEqual(view["text"], "")


@override_settings(WEB_TERMINAL=True)
class DomainsAndFilesViewsTests(TestCase):
    def setUp(self):
        project = Project.objects.create(name="Demo", slug="demo")
        self.env = Environment.objects.create(name="Production", slug="production", project=project)
        self.app = Application.objects.create(scope=self.env, slug="api", image="nginx:alpine")
        self.operator = get_user_model().objects.create_user("operator", is_staff=True)
        self.client.force_login(self.operator)

    @override_settings(ROUTE_TLS=True, TRAEFIK_CERT_RESOLVER="letsencrypt")
    def test_save_multiple_domains_and_generate_letsencrypt_routes(self):
        url = reverse("deployments:domains", args=[self.app.pk])
        self.assertRedirects(self.client.post(url, {
            "hostnames": "API.Example.org.\nbackup.example.org\napi.example.org",
        }), url, fetch_redirect_response=False)
        values = snapshot(self.app)
        self.assertEqual(values["domains"], ["api.example.org", "backup.example.org"])
        spec = make_spec(Deployment(application=self.app, spec=values), self.app.image)
        router = f"traefik.http.routers.{spec.name}"
        self.assertEqual(spec.labels[f"{router}.rule"], "Host(`api.example.org`) || Host(`backup.example.org`)")
        self.assertEqual(spec.labels[f"{router}.tls.certresolver"], "letsencrypt")
        self.assertEqual(spec.labels[f"{router}.tls.domains[1].main"], "backup.example.org")

    def test_rejects_assigned_domains_and_rule_injection(self):
        other = Application.objects.create(scope=self.env, slug="web")
        other.domains.create(hostname="claimed.example.org")
        url = reverse("deployments:domains", args=[self.app.pk])
        with patch("apps.deployments.views.owned_service", return_value=None):
            claimed = self.client.post(url, {"hostnames": "claimed.example.org"})
            invalid = self.client.post(url, {"hostnames": "bad.example.org`) || Host(`other.org"})
        self.assertContains(claimed, "already assigned")
        self.assertContains(invalid, "Enter DNS hostnames")
        self.assertFalse(self.app.domains.exists())

    def test_rollback_domains_must_still_be_assigned_and_not_routed_elsewhere(self):
        manager = Mock()
        with self.assertRaisesMessage(DomainError, "Assign api.example.org"):
            validate_routing(manager, self.app, ["api.example.org"])
        self.app.domains.create(hostname="api.example.org")
        manager.list_services.return_value = [
            ServiceInfo("another", "previous-service", "nginx", "replicated", 1, 1, labels={
                "waggle.app-id": "999",
                "traefik.http.routers.previous-service.rule": "Host(`api.example.org`)",
            }),
        ]
        with self.assertRaisesMessage(DomainError, "still routed by previous-service"):
            validate_routing(manager, self.app, ["api.example.org"])

    def test_native_rollback_checks_the_previous_swarm_spec_before_updating(self):
        with patch("apps.deployments.services.get_orchestrator") as orchestrator:
            manager = orchestrator.return_value
            manager.get_service.return_value = ServiceInfo(
                "service-id", "api", "nginx", "replicated", 1, 1,
                labels={"waggle.app-id": str(self.app.pk)},
            )
            manager.previous_service_labels.return_value = {
                "traefik.http.routers.api.rule": "Host(`old.example.org`)",
            }
            with self.assertRaisesMessage(DeploymentError, "Assign old.example.org"):
                rollback(self.app)
            manager.rollback_service.assert_not_called()

    def test_file_browser_requires_csrf_and_operator_and_uses_guarded_transport(self):
        url = reverse("deployments:browse_files", args=[self.app.pk])
        payload = {"action": "list", "path": "/app", "task": "worker-task"}
        with patch("apps.deployments.terminal_views.run_terminal", return_value=result("file\0config\0")) as run:
            reply = self.client.post(url, payload)
            self.assertEqual(reply.json()["entries"][0]["path"], "/app/config")
            self.assertIn("no-store", reply["Cache-Control"])
            self.assertEqual(run.call_args.args[2], "worker-task")
            enforced = Client(enforce_csrf_checks=True)
            enforced.force_login(self.operator)
            self.assertEqual(enforced.post(url, payload).status_code, 403)
            viewer = get_user_model().objects.create_user("viewer")
            self.client.force_login(viewer)
            self.assertEqual(self.client.post(url, payload).status_code, 403)
            run.assert_called_once()
