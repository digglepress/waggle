import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.core.backups import BackupError, copy_database


class Command(BaseCommand):
    help = "Create an online backup of Waggle's SQLite database at a new file path."

    def add_arguments(self, parser):
        parser.add_argument("destination", type=Path)

    def handle(self, *args, **options):
        db = connections["default"]
        if db.vendor != "sqlite":
            raise CommandError("backup_data requires SQLite.")
        source = Path(db.settings_dict["NAME"])
        destination = options["destination"]
        try:
            copy_database(source, destination)
        except (BackupError, OSError, sqlite3.Error) as exc:
            raise CommandError(f"Backup failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Backed up Waggle to {destination}"))
