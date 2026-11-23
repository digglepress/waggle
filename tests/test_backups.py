import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.core.backups import BackupError, copy_database


class BackupTests(SimpleTestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        self.source = self.folder / "active.sqlite3"
        self.backup = self.folder / "backup.sqlite3"
        self.live = sqlite3.connect(self.source)
        self.addCleanup(self.live.close)
        self.live.execute("PRAGMA journal_mode=WAL")
        self.live.execute("PRAGMA wal_autocheckpoint=0")
        self.live.executescript(
            "CREATE TABLE auth_user (username TEXT);"
            "CREATE TABLE django_migrations (name TEXT);"
            "CREATE TABLE deployments_deployment (state TEXT, error TEXT);"
            "CREATE TABLE servers_server (status TEXT, bootstrap_error TEXT, last_error TEXT);"
            "INSERT INTO auth_user VALUES ('operator');"
            "INSERT INTO deployments_deployment VALUES ('running', ''), ('submitted', '');"
            "INSERT INTO servers_server VALUES ('provisioning', '', ''), ('ready', '', '');"
        )

    def test_online_backup_includes_uncheckpointed_wal_and_is_standalone(self):
        self.assertTrue(self.source.with_name(self.source.name + "-wal").exists())
        copy_database(self.source, self.backup)
        with closing(sqlite3.connect(self.backup)) as database:
            self.assertEqual(database.execute("SELECT username FROM auth_user").fetchone(),
                             ("operator",))
            self.assertEqual(database.execute("PRAGMA journal_mode").fetchone(), ("delete",))
        self.assertFalse(self.backup.with_name(self.backup.name + "-wal").exists())

    def test_restore_preserves_completed_work_but_clears_in_flight_jobs(self):
        copy_database(self.source, self.backup)
        data_dir = self.folder / "restore"
        call_command("restore_data", str(self.backup), "--data-dir", str(data_dir))
        with closing(sqlite3.connect(data_dir / "waggle.sqlite3")) as database:
            self.assertEqual(database.execute(
                "SELECT state FROM deployments_deployment ORDER BY rowid"
            ).fetchall(), [("failed",), ("submitted",)])
            self.assertEqual(database.execute(
                "SELECT status FROM servers_server ORDER BY rowid"
            ).fetchall(), [("error",), ("ready",)])
        with self.assertRaises(CommandError):
            call_command("restore_data", str(self.backup), "--data-dir", str(data_dir))

    def test_backup_does_not_replace_existing_files(self):
        self.backup.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            copy_database(self.source, self.backup)
        self.assertEqual(self.backup.read_bytes(), b"existing")

    def test_restore_requires_a_waggle_database(self):
        source = self.folder / "other.db"
        with closing(sqlite3.connect(source)) as database:
            database.execute("CREATE TABLE other (id INT)")
        with self.assertRaises(BackupError):
            copy_database(source, self.backup, restore=True)
        self.assertFalse(self.backup.exists())
