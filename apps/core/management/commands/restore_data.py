import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.core.backups import BackupError, copy_database


class Command(BaseCommand):
    help = "Restore a Waggle backup into a separate data directory without overwriting live data."

    def add_arguments(self, parser):
        parser.add_argument("backup", type=Path)
        parser.add_argument("--data-dir", type=Path, required=True)

    def handle(self, *args, **options):
        data_dir = options["data_dir"]
        destination = data_dir / "waggle.sqlite3"
        active = Path(connections["default"].settings_dict["NAME"])
        if destination.resolve() == active.resolve():
            raise CommandError("Restore into a new data directory, not the active database.")
        try:
            data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if any(data_dir.glob("*.sqlite3*")):
                raise BackupError("The destination already contains SQLite data or a task queue.")
            copy_database(options["backup"], destination, restore=True)
        except (BackupError, OSError, sqlite3.Error) as exc:
            raise CommandError(f"Restore failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Restored Waggle to {destination}"))
        self.stdout.write("Keep the original secret key. Point WAGGLE_DATA_DIR at the new directory.")
