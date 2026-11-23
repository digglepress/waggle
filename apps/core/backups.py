"""Copy a live SQLite database without depending on its WAL or queue files."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path


class BackupError(RuntimeError):
    pass


def copy_database(source: Path, destination: Path, *, restore: bool = False) -> None:
    """Create a standalone database at a new path; never replace an existing file."""
    source_uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as origin:
        # Only remove destination on failure if this invocation created it.
        os.close(os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        try:
            with closing(sqlite3.connect(destination)) as copy:
                origin.backup(copy, pages=128)
                copy.execute("PRAGMA trusted_schema=OFF")
                copy.execute("PRAGMA journal_mode=DELETE")
                if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise BackupError("The database failed SQLite integrity checks.")
                tables = {name for (name,) in copy.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                )}
                if not {"auth_user", "django_migrations"}.issubset(tables):
                    raise BackupError("The file is not a Waggle database.")
                if restore:
                    _discard_in_flight_jobs(copy, tables)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def _discard_in_flight_jobs(copy: sqlite3.Connection, tables: set[str]) -> None:
    """The restored data directory does not have the original Huey queue."""
    message = "Interrupted by database restore. Check Swarm before retrying."
    with copy:
        if "deployments_deployment" in tables:
            copy.execute(
                "UPDATE deployments_deployment SET state = 'failed', error = ? "
                "WHERE state IN ('queued', 'running')",
                (message,),
            )
        if "servers_server" in tables:
            copy.execute(
                "UPDATE servers_server SET status = 'error', bootstrap_error = ?, last_error = ? "
                "WHERE status IN ('pending', 'provisioning')",
                (message, message),
            )
