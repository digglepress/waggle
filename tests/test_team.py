from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class TeamTests(TestCase):
    def test_owners_can_add_and_disable_members(self):
        users = get_user_model().objects
        self.client.force_login(users.create_superuser("owner", password="password"))
        result = self.client.post(reverse("core:team"), {"username": "new-operator", "is_staff": "on"})
        self.assertContains(result, "shown once")
        self.assertIn("no-store", result["Cache-Control"])
        member = users.get(username="new-operator")
        self.assertTrue(member.is_staff)
        self.assertFalse(member.is_superuser)
        self.client.post(reverse("core:team_member", args=[member.pk]), {"action": "disable"})
        member.refresh_from_db()
        self.assertFalse(member.is_active)

    def test_viewers_can_read_projects_but_cannot_create_services_or_access_team(self):
        users = get_user_model().objects
        self.client.force_login(users.create_user("viewer"))
        self.assertContains(self.client.get(reverse("deployments:index")), "Projects")
        self.assertEqual(self.client.post(reverse("deployments:new_project"), {"name": "Forbidden"}).status_code, 302)
        self.assertEqual(self.client.get(reverse("core:team")).status_code, 403)
