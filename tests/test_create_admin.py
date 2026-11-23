from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class CreateAdminTests(TestCase):
    def test_generated_credentials_bootstrap_once(self):
        output = StringIO()
        call_command("create_admin", "--generate", username="new-admin", stdout=output)
        password = output.getvalue().split("One-time setup password: ", 1)[1].strip()
        user = get_user_model().objects.get(username="new-admin")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(password))
        output = StringIO()
        call_command("create_admin", "--generate", username="new-admin", stdout=output)
        user.refresh_from_db()
        self.assertTrue(user.check_password(password))
        self.assertNotIn(password, output.getvalue())

    def test_creates_a_superuser(self):
        call_command("create_admin", username="root", password="correct-horse", verbosity=0)
        user = get_user_model().objects.get(username="root")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password("correct-horse"))

    def test_is_idempotent_and_updates_the_password(self):
        call_command("create_admin", username="root", password="first-password", verbosity=0)
        call_command("create_admin", username="root", password="second-password", verbosity=0)
        self.assertEqual(get_user_model().objects.filter(username="root").count(), 1)
        self.assertTrue(
            get_user_model().objects.get(username="root").check_password("second-password")
        )

    def test_requires_a_password(self):
        with self.assertRaises(CommandError):
            call_command("create_admin", username="root", password="", verbosity=0)

    def test_rejects_short_passwords(self):
        with self.assertRaises(CommandError):
            call_command("create_admin", username="root", password="short", verbosity=0)
