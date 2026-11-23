from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.deployments.models import Application, Deployment, Environment, Project
from apps.servers.models import Server


class MetricsTests(TestCase):
    def make_service(self, slug):
        project = Project.objects.create(name="Demo", slug="demo")
        scope = Environment.objects.create(project=project, name="Production", slug="production")
        return Application.objects.create(slug=slug, image="alpine:3", scope=scope)

    @override_settings(METRICS_TOKEN="")
    def test_metrics_are_not_exposed_by_default(self):
        self.assertEqual(self.client.get(reverse("core:metrics")).status_code, 404)

    @override_settings(METRICS_TOKEN="metrics-test-token")
    def test_metrics_require_the_configured_token(self):
        url = reverse("core:metrics")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, headers={"Authorization": "Bearer bad"}).status_code,
                         403)

    @override_settings(METRICS_TOKEN="metrics-test-token")
    def test_scrape_includes_aggregate_counts_without_resource_names(self):
        app = self.make_service("some-private-app")
        Deployment.objects.create(application=app, spec={})
        Server.objects.create(name="some-private-node", address="192.0.2.1", status="ready")
        response = self.client.get(reverse("core:metrics"),
                                   headers={"Authorization": "Bearer metrics-test-token"})
        self.assertContains(response, "waggle_applications 1")
        self.assertContains(response, 'waggle_deployments{state="queued"} 1')
        self.assertContains(response, 'waggle_servers{state="ready"} 1')
        self.assertNotContains(response, "some-private-")
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(ORCHESTRATOR="orchestrator.fake:FakeOrchestrator")
    def test_empty_dashboard_offers_onboarding_to_staff_only(self):
        user = get_user_model().objects.create_user("operator")
        self.client.force_login(user)
        url = reverse("core:dashboard")
        self.assertNotContains(self.client.get(url), "Deploy your first application")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.assertContains(self.client.get(url), "Deploy your first application")
        self.make_service("demo")
        self.assertNotContains(self.client.get(url), "Deploy your first application")
