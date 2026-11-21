"""Create or update the Waggle administrator.

Used by ``deploy/install.sh`` so a fresh install has a way in without an
interactive TTY. Safe to run repeatedly:

    WAGGLE_ADMIN_USER=admin WAGGLE_ADMIN_PASSWORD=... python manage.py create_admin
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the admin user from WAGGLE_ADMIN_* environment variables."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=os.environ.get("WAGGLE_ADMIN_USER", "admin"),
            help="Defaults to WAGGLE_ADMIN_USER or 'admin'.",
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("WAGGLE_ADMIN_PASSWORD"),
            help="Defaults to WAGGLE_ADMIN_PASSWORD. Required.",
        )
        parser.add_argument(
            "--email",
            default=os.environ.get("WAGGLE_ADMIN_EMAIL", ""),
            help="Defaults to WAGGLE_ADMIN_EMAIL.",
        )

    def handle(self, *args, **options):
        username = options["username"]
        password = options["password"]
        email = options["email"]

        if not password:
            raise CommandError(
                "No password given. Set WAGGLE_ADMIN_PASSWORD or pass --password."
            )
        if len(password) < 8:
            raise CommandError("The admin password must be at least 8 characters.")

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": email}
        )
        user.is_staff = True
        user.is_superuser = True
        if email:
            user.email = email
        if created or not user.check_password(password):
            user.set_password(password)
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} admin user '{username}'."
            )
        )
