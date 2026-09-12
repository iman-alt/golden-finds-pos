"""
Backups.

The shop's entire business lives in one SQLite file. If that machine is
stolen or its disk dies, the business loses its stock levels, its debtors
and its history at once. So this is not an optional extra.

Backups use SQLite's own online backup API rather than copying the file.
A plain copy of a database in WAL mode can catch it mid-write and produce
a file that looks fine and will not open - and nobody discovers that
until the day they need it. The backup API takes a consistent snapshot
while the till keeps trading.

The destination is just a folder. Point it at a OneDrive or Google Drive
folder and the sync client carries it off the machine, which is what
makes the copy worth having.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


class BackupError(Exception):
    """Raised when a backup or restore cannot be completed."""


def create_backup(database_path, destination_dir, *, keep=30):
    """
    Writes a consistent snapshot of the database into `destination_dir`,
    named with the date and time, and prunes old ones.

    Returns the Path of the file written.
    """
    database_path = Path(database_path)
    if not database_path.exists():
        raise BackupError(f"No database at {database_path}")

    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    target = destination_dir / f"golden-finds_{stamp}.db"

    source = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            # The online backup API: consistent even while the till writes.
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    verify_backup(target)
    prune_backups(destination_dir, keep=keep)
    return target


def verify_backup(path):
    """
    Opens the backup and runs an integrity check.

    An unverified backup is not a backup - it is a file you hope is a
    backup. This is the difference between the two.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise BackupError(f"Backup at {path} is missing or empty.")

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as err:
        raise BackupError(f"Backup at {path} cannot be opened: {err}")

    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError(f"Backup at {path} failed its integrity check.")

        # A structurally valid but empty database would also pass the
        # check above, so confirm the tables are actually there.
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = {"products", "sales", "stock_movements", "users"} - tables
        if missing:
            raise BackupError(
                f"Backup at {path} is missing tables: {', '.join(sorted(missing))}"
            )
    except sqlite3.DatabaseError as err:
        # A truncated or corrupted file surfaces here. Callers should only
        # ever have to catch BackupError.
        raise BackupError(f"Backup at {path} is not a valid database: {err}")
    finally:
        conn.close()

    return True


def prune_backups(destination_dir, keep=30):
    """Keeps the newest `keep` backups and deletes the rest."""
    backups = sorted(
        Path(destination_dir).glob("golden-finds_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for old in backups[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            pass
    return removed


def list_backups(destination_dir):
    destination_dir = Path(destination_dir)
    if not destination_dir.exists():
        return []
    return sorted(
        (
            {
                "path": path,
                "when": datetime.fromtimestamp(path.stat().st_mtime),
                "size_kb": path.stat().st_size // 1024,
            }
            for path in destination_dir.glob("golden-finds_*.db")
        ),
        key=lambda entry: entry["when"],
        reverse=True,
    )


def last_backup_age(destination_dir):
    """
    How long since the most recent backup, or None if there are none.
    The dashboard uses this to say so out loud when backups have stopped
    happening - a backup job that silently stopped months ago is the
    usual way this kind of system loses data.
    """
    backups = list_backups(destination_dir)
    if not backups:
        return None
    return datetime.now() - backups[0]["when"]


def restore_backup(backup_path, database_path):
    """
    Restores a backup over the live database.

    The current database is moved aside first rather than overwritten, so
    a restore of the wrong file is itself recoverable.
    """
    backup_path = Path(backup_path)
    database_path = Path(database_path)

    verify_backup(backup_path)

    if database_path.exists():
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        aside = database_path.with_name(f"{database_path.stem}.before-restore-{stamp}.db")
        try:
            os.replace(database_path, aside)
        except PermissionError:
            # Windows will not rename a file another process still holds
            # open. That means the app is still running, and restoring
            # underneath it would corrupt what it is holding.
            raise BackupError(
                "The database is in use. Stop the app before restoring."
            )

    # Copy through SQLite rather than the filesystem, so the restored file
    # is a clean database with no leftover WAL or journal alongside it.
    source = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(database_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    for suffix in ("-wal", "-shm"):
        stale = Path(str(database_path) + suffix)
        if stale.exists():
            stale.unlink()

    return database_path
