import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core.logs import deployment_log_path
from apps.deployments.build_logs import run_build
from apps.deployments.models import (
    Application,
    Deployment,
    Environment,
    Project,
    SourceConnection,
    Tag,
)
from apps.deployments.services import make_spec, snapshot


class ServiceOperationsTests(TestCase):
    def setUp(self):
        self.enterContext(override_settings(BUILD_LOG_DIR=Path(self.enterContext(TemporaryDirectory()))))
        project = Project.objects.create(name="Example", slug="example")
        scope = Environment.objects.create(name="Production", slug="production", project=project)
        source = SourceConnection.objects.create(name="Docker Hub", host="docker.io", kind="registry")
        self.app = Application.objects.create(slug="api", scope=scope, connection=source, image="nginx:alpine")
        self.client.force_login(get_user_model().objects.create_user("operator", is_staff=True))

    def test_variables_and_deployment_history_are_encrypted_at_rest(self):
        self.app.environment = {"MARKER": "plaintext-marker"}
        self.app.save(update_fields=["environment"])
        job = Deployment.objects.create(application=self.app, spec=snapshot(self.app))
        with connection.cursor() as cursor:
            for table, field in (("application", "environment"), ("deployment", "spec")):
                cursor.execute(f"SELECT {field} FROM deployments_{table}")
                ciphertext = cursor.fetchone()[0]
                self.assertTrue(ciphertext.startswith("enc:v1:"))
                self.assertNotIn("plaintext-marker", ciphertext)
        job.refresh_from_db()
        self.assertEqual(job.spec["environment"], self.app.environment)

    def test_history_rollback_queues_the_selected_snapshot_not_current_settings(self):
        old = Deployment.objects.create(
            application=self.app, spec=snapshot(self.app), state="submitted", image="nginx:alpine",
        )
        self.app.environment = {"CHANGE": "new"}
        self.app.save(update_fields=["environment"])
        with patch("apps.deployments.views.deploy_application") as enqueue:
            result = self.client.post(reverse("deployments:rollback_to", args=[self.app.pk, old.pk]))
        self.assertEqual(result.status_code, 302)
        rollback = Deployment.objects.get(rollback_target=old)
        self.assertEqual(rollback.spec, old.spec)
        self.assertEqual(rollback.image, old.image)
        enqueue.assert_called_once_with(rollback.pk)

    @override_settings(ROUTE_TLS=True, TRAEFIK_CERT_RESOLVER="letsencrypt")
    def test_mount_health_and_certificate_resolver_are_in_swarm_spec(self):
        self.app.domains.create(hostname="api.example.org")
        self.app.volumes = [{"name": "data", "target": "/data", "read_only": True}]
        self.app.healthcheck, self.app.health_interval = "true", 23
        job = Deployment(application=self.app, spec=snapshot(self.app))
        spec = make_spec(job, self.app.image)
        self.assertEqual(spec.mounts[0].target, "/data")
        self.assertTrue(spec.mounts[0].read_only)
        self.assertEqual(spec.healthcheck.interval_s, 23)
        self.assertEqual(spec.labels[f"traefik.http.routers.{spec.name}.tls.certresolver"], "letsencrypt")

    def test_deployment_stream_resumes_and_ends(self):
        job = Deployment.objects.create(application=self.app, spec=snapshot(self.app), state="submitted")
        path = deployment_log_path(job.pk)
        first = b"2026-01-01T00:00:00+00:00 first\n"
        path.write_bytes(first + b"2026-01-01T00:00:01+00:00 second\n")
        result = self.client.get(
            reverse("deployments:deployment_stream", args=[self.app.pk, job.pk]),
            headers={"Last-Event-ID": str(len(first))},
        )
        content = b"".join(result.streaming_content).decode()
        self.assertNotIn("first", content)
        self.assertIn("2026-01-01T00:00:01+00:00 second", content)
        self.assertIn("event: done", content)

    def test_tags_and_notification_inbox(self):
        self.app.tags.add(Tag.objects.create(name="backend"))
        self.assertContains(self.client.get(reverse("deployments:index") + "?tag=backend"), "Services tagged backend")
        Deployment.objects.create(application=self.app, spec=snapshot(self.app), state="failed")
        url = reverse("deployments:notifications")
        self.assertContains(self.client.get(url), "Failed")
        self.client.post(url)
        self.assertNotContains(self.client.get(url), '<span class="tag">New</span>')


class TimestampedBuildTests(SimpleTestCase):
    def test_build_output_is_timestamped(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "build.log"
            run_build([sys.executable, "-c", "print('sample build output')"],
                      env=None, log_path=path, timeout=10)
            self.assertRegex(path.read_text(), r"\d{4}-\d{2}-\d{2}T.*\+00:00 sample build output")
