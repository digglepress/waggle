from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditEvent
from apps.core.version import VERSION
from apps.deployments.models import Project


class SecurityHeadersTests(TestCase):
    def test_security_headers_and_request_id_are_set(self):
        response = self.client.get(reverse("login"), headers={"X-Request-ID": "abc123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Request-ID"], "abc123")
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Permissions-Policy"].split(",")[0], "camera=()")

    def test_invalid_request_id_is_replaced(self):
        response = self.client.get(reverse("login"), headers={"X-Request-ID": "not valid!"})
        self.assertNotEqual(response["X-Request-ID"], "not valid!")
        self.assertEqual(len(response["X-Request-ID"]), 32)


class ProbeTests(TestCase):
    def test_readiness_reports_the_version(self):
        response = self.client.get(reverse("core:ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], VERSION)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_health_reports_the_version(self):
        response = self.client.get(reverse("core:health"))
        self.assertEqual(response.json(), {"status": "ok", "service": "waggle", "version": VERSION})


class AuditTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("operator", is_staff=True)
        self.client.force_login(self.staff)

    def test_project_creation_is_audited(self):
        response = self.client.post(reverse("deployments:new_project"), {"name": "Payments"})
        project = Project.objects.get(name="Payments")
        self.assertEqual(response.status_code, 302)
        event = AuditEvent.objects.get(action="project.create")
        self.assertEqual(event.actor, self.staff)
        self.assertEqual(event.target_type, "deployments.project")
        self.assertEqual(event.target_id, str(project.pk))
        self.assertTrue(event.request_id)

    def test_audit_log_requires_staff(self):
        viewer = get_user_model().objects.create_user("viewer")
        self.client.force_login(viewer)
        response = self.client.get(reverse("core:audit"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("core:audit")).status_code, 200)
