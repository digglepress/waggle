from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.core.models import APIToken, AuditEvent
from apps.deployments.models import Application, Deployment, Environment, Project


class APITests(TestCase):
    def setUp(self):
        project = Project.objects.create(name="Demo", slug="demo")
        scope = Environment.objects.create(project=project, name="Production", slug="production")
        self.app = Application.objects.create(slug="api", image="alpine:3", scope=scope)
        self.app.domains.create(hostname="api.example.org")
        self.read_token, self.read_raw = APIToken.issue("dashboard", ["read"])
        self.deploy_token, self.deploy_raw = APIToken.issue("ci", ["read", "deploy"])

    def auth(self, raw):
        return {"Authorization": f"Bearer {raw}"}

    def test_missing_and_invalid_tokens_are_rejected(self):
        url = reverse("api:services")
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.auth("nope")).status_code, 401)
        self.read_token.active = False
        self.read_token.save()
        self.assertEqual(self.client.get(url, headers=self.auth(self.read_raw)).status_code, 401)

    def test_read_scope_can_list_services_but_not_deploy(self):
        url = reverse("api:services")
        response = self.client.get(url, headers=self.auth(self.read_raw))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["services"][0]["slug"], "api")
        self.assertEqual(response.json()["services"][0]["domains"], ["api.example.org"])

        deploy_url = reverse("api:deploy", args=[self.app.pk])
        self.assertEqual(self.client.post(deploy_url, headers=self.auth(self.read_raw)).status_code, 403)
        self.assertFalse(Deployment.objects.exists())

    def test_deploy_token_queues_a_deployment_and_writes_an_audit_event(self):
        url = reverse("api:deploy", args=[self.app.pk])
        with patch("apps.deployments.queueing.deploy_application") as enqueue:
            response = self.client.post(url, headers=self.auth(self.deploy_raw))
        self.assertEqual(response.status_code, 202)
        job = Deployment.objects.get()
        self.assertEqual(job.state, Deployment.State.QUEUED)
        self.assertEqual(response.json()["deployment"]["id"], job.pk)
        enqueue.assert_called_once_with(job.pk)
        event = AuditEvent.objects.get(action="deployment.deploy")
        self.assertEqual(event.detail["via"], "api")
        self.assertEqual(event.target_id, str(self.app.pk))

    def test_duplicate_deploy_is_a_conflict(self):
        url = reverse("api:deploy", args=[self.app.pk])
        with patch("apps.deployments.queueing.deploy_application"):
            self.assertEqual(self.client.post(url, headers=self.auth(self.deploy_raw)).status_code, 202)
            self.assertEqual(self.client.post(url, headers=self.auth(self.deploy_raw)).status_code, 409)

    def test_rollback_to_a_specific_deployment(self):
        target = Deployment.objects.create(
            application=self.app, spec={}, image="alpine:3", state=Deployment.State.SUBMITTED,
        )
        url = reverse("api:rollback", args=[self.app.pk])
        with patch("apps.deployments.queueing.deploy_application"):
            response = self.client.post(
                url, data={"deployment_id": target.pk},
                content_type="application/json", headers=self.auth(self.deploy_raw),
            )
        self.assertEqual(response.status_code, 202)
        job = Deployment.objects.exclude(pk=target.pk).get()
        self.assertEqual(job.operation, Deployment.Operation.ROLLBACK)
        self.assertEqual(job.rollback_target, target)
        self.assertEqual(job.image, "alpine:3")

    def test_deployment_status_and_token_identity(self):
        job = Deployment.objects.create(
            application=self.app, spec={}, image="alpine:3", state=Deployment.State.SUBMITTED,
        )
        response = self.client.get(
            reverse("api:deployment", args=[job.pk]), headers=self.auth(self.read_raw),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deployment"]["state"], "submitted")
        me = self.client.get(reverse("api:me"), headers=self.auth(self.read_raw)).json()
        self.assertEqual(me["scopes"], ["read"])
