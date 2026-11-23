from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.deployments.envvars import format_dotenv, parse_dotenv
from apps.deployments.models import Application, Deployment, Environment, Project, SourceConnection
from apps.deployments.services import build_image, deploy, service_name, snapshot


class ServiceWorkflowTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("operator", is_staff=True))

    def test_project_environment_registry_service_and_both_variable_editors(self):
        response = self.client.post(reverse("deployments:new_project"), {"name": "Facebook"})
        project = Project.objects.get(name="Facebook")
        self.assertRedirects(response, reverse("deployments:project", args=[project.pk]))
        response = self.client.post(reverse("deployments:new_environment", args=[project.pk]),
                                    {"name": "Production"})
        env = Environment.objects.get(project=project)
        self.assertRedirects(response, reverse("deployments:environment", args=[env.pk]))
        self.assertContains(self.client.get(reverse("deployments:choose_source", args=[env.pk])),
                            "Use Git repository")

        form_url = reverse("deployments:new", args=[env.pk, "registry"])
        connect_url = reverse("deployments:new_connection", args=["registry"]) + f"?environment={env.pk}"
        self.assertRedirects(self.client.post(connect_url, {
            "name": "Docker Hub", "host": "docker.io",
        }), form_url)
        registry = SourceConnection.objects.get()
        payload = {"slug": "api", "connection": registry.pk, "image": "nginx:alpine",
                   "port": 80, "replicas": 1}
        wrong = self.client.post(form_url, {**payload, "image": "ghcr.io/org/app:1"})
        self.assertContains(wrong, "The source host must match docker.io.")
        response = self.client.post(form_url, payload)
        app = Application.objects.get(scope=env, slug="api")
        variables_url = reverse("deployments:variables", args=[app.pk])
        self.assertRedirects(response, variables_url)

        text = 'APP_MODE=production\nNOTE="first\\nsecond"\nLITERAL=$APP_MODE # a comment'
        detail_url = reverse("deployments:detail", args=[app.pk])
        self.assertRedirects(
            self.client.post(variables_url + "?mode=text", {"variables": text}), detail_url,
        )
        form_page = self.client.get(variables_url)
        self.assertContains(form_page, "Add variable row")
        self.assertContains(form_page, "first\nsecond")
        self.assertRedirects(self.client.post(variables_url, {
            "variables-TOTAL_FORMS": 4, "variables-INITIAL_FORMS": 3,
            "variables-0-name": "APP_MODE", "variables-0-value": "live",
            "variables-1-name": "NOTE", "variables-1-value": "first\nsecond",
            "variables-2-name": "LITERAL", "variables-2-value": "$APP_MODE",
            "variables-2-DELETE": "on",
            "variables-3-name": "EXTRA", "variables-3-value": "ok",
        }), detail_url)
        app.refresh_from_db()
        self.assertEqual(app.environment, {"APP_MODE": "live", "NOTE": "first\nsecond", "EXTRA": "ok"})
        self.assertContains(self.client.get(variables_url + "?mode=text"), "APP_MODE=live")
        self.assertEqual(parse_dotenv(format_dotenv(app.environment)), app.environment)

        stage = Environment.objects.create(project=project, name="Staging", slug="staging")
        self.client.post(reverse("deployments:new", args=[stage.pk, "registry"]), payload)
        another = Application.objects.get(scope=stage)
        self.assertNotEqual(service_name(app), service_name(another))

    def test_private_registry_credentials_are_encrypted_and_scoped_to_deploy(self):
        self.client.post(reverse("deployments:new_connection", args=["registry"]), {
            "name": "Private", "host": "ghcr.io", "username": "operator", "credential": "example-token",
        })
        registry = SourceConnection.objects.get()
        with connection.cursor() as cursor:
            cursor.execute("SELECT credential FROM deployments_sourceconnection WHERE id = %s",
                           [registry.pk])
            self.assertTrue(cursor.fetchone()[0].startswith("enc:v1:"))
        project = Project.objects.create(name="Demo", slug="demo")
        env = Environment.objects.create(project=project, name="Live", slug="live")
        app = Application.objects.create(
            scope=env, slug="api", connection=registry, image="ghcr.io/org/api:1",
        )
        job = Deployment(application=app, spec=snapshot(app))
        self.assertNotIn("example-token", str(job.spec))
        with patch("apps.deployments.services.get_orchestrator") as get_manager:
            manager = get_manager.return_value
            manager.get_service.return_value = None
            deploy(job)
            spec = manager.create_service.call_args.args[0]
        self.assertEqual(spec.registry_auth.host, "ghcr.io")
        self.assertEqual(spec.registry_auth.password, "example-token")
        self.assertNotIn("example-token", repr(spec))

    def test_git_credentials_are_not_placed_in_cli_arguments(self):
        git = SourceConnection(kind="git", name="GitHub", host="github.com", credential="token")
        project = Project.objects.create(name="Demo", slug="demo")
        env = Environment.objects.create(project=project, name="Live", slug="live")
        app = Application.objects.create(
            scope=env, slug="api", repository="https://github.com/org/repo.git", revision="a" * 40,
        )
        app.build_target = "production"
        app.submodule_connections.add(SourceConnection.objects.create(
            kind="git", name="Private submodule", host="git.example.org", credential="submodule-secret",
        ))
        job = Deployment(pk=12345, application=app, spec=snapshot(app))

        def run(command, **kwargs):
            self.assertIn("id=GIT_AUTH_TOKEN.github.com,env=WAGGLE_GIT_AUTH_TOKEN", command)
            self.assertIn("id=GIT_AUTH_TOKEN.git.example.org,env=WAGGLE_GIT_AUTH_TOKEN_1", command)
            self.assertEqual(command[command.index("--target") + 1], "production")
            self.assertNotIn("token", " ".join(command))
            self.assertNotIn("submodule-secret", " ".join(command))
            self.assertEqual(kwargs["env"]["WAGGLE_GIT_AUTH_TOKEN"], "token")
            self.assertEqual(kwargs["env"]["WAGGLE_GIT_AUTH_TOKEN_1"], "submodule-secret")
            metadata = Path(command[command.index("--metadata-file") + 1])
            metadata.write_text('{"containerimage.digest":"sha256:' + "b" * 64 + '"}', encoding="utf-8")

        with TemporaryDirectory() as folder, override_settings(
            BUILD_LOG_DIR=Path(folder), REGISTRY_HOST="registry.example.org",
        ), patch("apps.deployments.services.run_build", side_effect=run):
            self.assertIn("@sha256:", build_image(job, git))
