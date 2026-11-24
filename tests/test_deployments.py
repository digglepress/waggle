from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.deployments.forms import ApplicationForm
from apps.deployments.models import Application, Deployment, Environment, Project, SourceConnection
from apps.deployments.services import build_image, deploy, make_spec, snapshot
from apps.deployments.tasks import _run
from orchestrator.fake import FakeOrchestrator
from orchestrator.types import ConvergenceResult


class DeploymentTests(TestCase):
    def setUp(self):
        self.enterContext(override_settings(BUILD_LOG_DIR=Path(self.enterContext(TemporaryDirectory()))))
        project = Project.objects.create(name="Demo", slug="demo")
        self.scope = Environment.objects.create(project=project, name="Production", slug="production")
        self.registry = SourceConnection.objects.create(kind="registry", name="Docker Hub", host="docker.io")
        self.app = Application.objects.create(
            slug="demo", scope=self.scope, connection=self.registry, legacy_service_name=True,
            image="traefik/whoami:latest",
            port=80, environment={"MODE": "test"}, secret_names=["db-password"],
        )
        self.app.domains.create(hostname="demo.example.org")
        self.staff = get_user_model().objects.create_user(username="operator", is_staff=True)

    def test_form_validates_service_sources(self):
        data = {
            "slug": "other", "image": "alpine:3",
            "port": 80, "replicas": 1, "connection": self.registry.pk, "secret_names": "[]",
        }
        form = ApplicationForm(data, scope=self.scope, kind="registry")
        self.assertTrue(form.is_valid(), form.errors)
        git = SourceConnection.objects.create(kind="git", name="Git", host="example.org")
        form = ApplicationForm({**data, "connection": git.pk, "repository": "https://example.org/demo.git"},
                               scope=self.scope, kind="git")
        self.assertIn("revision", form.errors)

    def test_non_staff_cannot_deploy(self):
        self.client.force_login(get_user_model().objects.create_user(username="viewer"))
        response = self.client.post(reverse("deployments:launch", args=[self.app.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Deployment.objects.exists())

    def test_queue_snapshots_configuration_and_rejects_duplicate(self):
        self.client.force_login(self.staff)
        path = reverse("deployments:launch", args=[self.app.pk])
        with patch("apps.deployments.queueing.deploy_application") as enqueue:
            response = self.client.post(path)
            self.client.post(path)
        job = Deployment.objects.get()
        self.assertRedirects(response, reverse("deployments:job", args=[self.app.pk, job.pk]))
        self.assertEqual(job.spec["environment"], {"MODE": "test"})
        enqueue.assert_called_once_with(job.pk)

    @override_settings(ROUTE_TLS=True, OBJECT_PREFIX="waggle")
    def test_routes_use_service_labels_and_no_published_port(self):
        self.app.memory_mb = 128
        self.app.cpu_millicores = 250
        self.app.healthcheck = "true"
        job = Deployment(application=self.app, spec=snapshot(self.app))
        spec = make_spec(job, self.app.image)
        self.assertEqual(spec.ports, ())
        self.assertEqual(spec.resources.memory_mb, 128)
        self.assertEqual(spec.resources.cpus, 0.25)
        self.assertEqual(spec.healthcheck.command, "true")
        self.assertEqual(spec.secrets[0].name, "db-password")
        self.assertEqual(spec.labels["traefik.http.routers.waggle-app-demo.rule"],
                         "Host(`demo.example.org`)")
        self.assertEqual(spec.labels["traefik.http.routers.waggle-app-demo.tls"], "true")

    def test_task_creates_then_updates_service(self):
        FakeOrchestrator.reset()
        fake = FakeOrchestrator()
        self.addCleanup(FakeOrchestrator.reset)
        with patch("apps.deployments.services.get_orchestrator", return_value=fake):
            initial = Deployment.objects.create(application=self.app, spec=snapshot(self.app))
            _run(initial.pk)
            initial.refresh_from_db()
            self.assertEqual(initial.state, Deployment.State.SUBMITTED)
            self.app.replicas = 3
            update = Deployment.objects.create(application=self.app, spec=snapshot(self.app))
            _run(update.pk)
            _run(update.pk)  # An already claimed task cannot run again.
        self.assertEqual(fake.get_service("waggle-app-demo").replicas_desired, 3)
        self.assertEqual(fake.call_names().count("update_service"), 1)

    def test_failed_rollout_is_rolled_back(self):
        FakeOrchestrator.reset()
        fake = FakeOrchestrator()
        self.addCleanup(FakeOrchestrator.reset)
        with patch("apps.deployments.services.get_orchestrator", return_value=fake):
            first = Deployment.objects.create(application=self.app, spec=snapshot(self.app))
            _run(first.pk)
            failed = ConvergenceResult(
                converged=False, desired=1, running=0, failed=1, elapsed_s=2.0,
                message="task exited with code 1",
            )
            with patch.object(fake, "wait_for_convergence", return_value=failed):
                second = Deployment.objects.create(application=self.app, spec=snapshot(self.app))
                _run(second.pk)
        second.refresh_from_db()
        self.assertEqual(second.state, Deployment.State.FAILED)
        self.assertIn("Rolled back", second.error)
        self.assertEqual(fake.call_names().count("rollback_service"), 1)

    def test_runtime_logs_escape_output_and_rollback_targets_owned_service(self):
        FakeOrchestrator.reset()
        fake = FakeOrchestrator()
        self.addCleanup(FakeOrchestrator.reset)
        job = Deployment.objects.create(application=self.app, spec=snapshot(self.app))
        self.client.force_login(self.staff)
        with patch("apps.deployments.services.get_orchestrator", return_value=fake):
            _run(job.pk)
            service = fake.get_service("waggle-app-demo")
            fake.log_lines[service.name] = ["<script>untrusted output</script>"]
            with patch("apps.deployments.views.get_orchestrator", return_value=fake):
                response = self.client.get(reverse("deployments:logs", args=[self.app.pk]))
                self.assertContains(response, "&lt;script&gt;")
                self.assertEqual(response["Cache-Control"], "no-store")
            undo = Deployment.objects.create(
                application=self.app, spec=snapshot(self.app),
                operation=Deployment.Operation.ROLLBACK,
            )
            _run(undo.pk)
            undo.refresh_from_db()
        self.assertEqual(undo.state, Deployment.State.SUBMITTED)
        self.assertEqual(fake.call_names().count("rollback_service"), 1)

    @override_settings(REGISTRY_HOST="registry.example.org:5000", OBJECT_PREFIX="waggle")
    def test_build_pushes_a_tag_but_deploys_by_digest(self):
        self.app.repository = "https://example.org/demo.git"
        self.app.revision = "a" * 40
        job = Deployment(pk=7, application=self.app, spec=snapshot(self.app))
        digest = "sha256:" + "b" * 64

        def build(command, **kwargs):
            self.assertEqual(command[-1], f"{self.app.repository}#{self.app.revision}")
            self.assertIn("--push", command)
            file = Path(command[command.index("--metadata-file") + 1])
            file.write_text('{"containerimage.digest":"' + digest + '"}', encoding="utf-8")

        with TemporaryDirectory() as temp, override_settings(BUILD_LOG_DIR=Path(temp)):
            with patch("apps.deployments.services.run_build", side_effect=build):
                self.assertEqual(build_image(job), f"registry.example.org:5000/waggle-app-demo@{digest}")

    @override_settings(REGISTRY_HOST="registry.example.org:5000")
    def test_built_images_get_swarm_pull_credentials(self):
        FakeOrchestrator.reset()
        fake = FakeOrchestrator()
        self.addCleanup(FakeOrchestrator.reset)
        git = SourceConnection.objects.create(kind="git", name="Git", host="example.org")
        SourceConnection.objects.create(
            kind="registry", name="Build registry", host="registry.example.org:5000",
            username="robot", credential="pull-secret",
        )
        self.app.image = ""
        self.app.repository = "https://example.org/demo.git"
        self.app.revision = "a" * 40
        self.app.connection = git
        self.app.save()
        job = Deployment(application=self.app, spec=snapshot(self.app))
        image = "registry.example.org:5000/waggle-service-1-demo@sha256:" + "c" * 64
        with patch("apps.deployments.services.get_orchestrator", return_value=fake), \
                patch("apps.deployments.services.build_image", return_value=image):
            deploy(job)
        spec = next(call[1][0] for call in fake.calls if call[0] == "create_service")
        self.assertEqual(spec.registry_auth.username, "robot")
        self.assertEqual(spec.registry_auth.password, "pull-secret")
        self.assertEqual(spec.registry_auth.host, "registry.example.org:5000")
