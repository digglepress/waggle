"""Reconcile the database with the actual swarm.

Run after ``migrate`` (the installer does), and any time you suspect drift:

    python manage.py sync_cluster
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.servers.services import reconcile_all


class Command(BaseCommand):
    help = "Register the local node and refresh every server from Docker Swarm."

    def handle(self, *args, **options):
        summary = reconcile_all()
        self.stdout.write(
            self.style.SUCCESS(
                "sync_cluster: "
                f"local={summary['local']} "
                f"refreshed={summary['refreshed']} "
                f"offline={summary['offline']} "
                f"failed={summary['failed']}"
            )
        )
