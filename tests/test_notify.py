from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.core import notify
from apps.deployments.models import Application, Deployment, Environment, Project


class NotifyTests(TestCase):
    def setUp(self):
        project = Project.objects.create(name="Demo", slug="demo")
        scope = Environment.objects.create(project=project, name="Production", slug="production")
        self.app = Application.objects.create(slug="api", image="alpine:3", scope=scope)

    def make_job(self, state, error=""):
        return Deployment.objects.create(
            application=self.app, spec={}, state=state, image="alpine:3", error=error,
        )

    @override_settings(NOTIFY_WEBHOOK_URL="")
    def test_no_webhook_is_silent(self):
        with patch("apps.core.notify._post") as post:
            notify.deployment_finished(self.make_job("failed", "boom"))
        post.assert_not_called()

    @override_settings(NOTIFY_WEBHOOK_URL="https://hooks.example.org/waggle")
    def test_failure_posts_a_slack_compatible_message(self):
        job = self.make_job("failed", "task exited with code 1")
        with patch("apps.core.notify._post") as post:
            notify.deployment_finished(job)
        url, payload = post.call_args.args
        self.assertEqual(url, "https://hooks.example.org/waggle")
        self.assertIn("failed: api", payload["text"])
        self.assertIn("task exited with code 1", payload["text"])

    @override_settings(NOTIFY_WEBHOOK_URL="https://hooks.example.org/waggle")
    def test_delivery_errors_never_escape(self):
        with patch("apps.core.notify._post", side_effect=OSError("offline")):
            notify.deployment_finished(self.make_job("failed", "boom"))
