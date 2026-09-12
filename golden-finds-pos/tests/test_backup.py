"""
Backup and restore.

These matter more than they look. The whole shop is one file, and an
untested backup is not a backup - so the tests actually restore from the
copies they take and check the data is still there.
"""

import sqlite3
from pathlib import Path

import pytest

from app.backup import (
    BackupError, create_backup, last_backup_age, list_backups,
    prune_backups, restore_backup, verify_backup,
)
from app.db import query_one, transaction
from app.services import products, sales


@pytest.fixture
def stocked(app, make_product, cashier):
    """A shop with a product and a sale, so a restore has something to prove."""
    product = make_product(name="Sugar 1kg", retail=15000, wholesale=14000,
                           stock_qty=30)
    with transaction() as conn:
        sales.record_sale(conn, cashier_id=cashier["id"],
                          items=[{"product_id": product["id"], "quantity": 4}],
                          payment_method="cash")
    return product


def test_backup_is_written_and_verified(app, stocked, tmp_path):
    path = create_backup(app.config["DATABASE"], tmp_path)

    assert path.exists()
    assert path.stat().st_size > 0
    assert verify_backup(path) is True


def test_the_backup_contains_the_data(app, stocked, tmp_path):
    path = create_backup(app.config["DATABASE"], tmp_path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        product = conn.execute(
            "SELECT * FROM products WHERE name = 'Sugar 1kg'"
        ).fetchone()
        assert product["stock_quantity"] == 26
        assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1
    finally:
        conn.close()


def test_backup_works_while_the_database_is_in_use(app, stocked, tmp_path):
    """
    A plain file copy of a WAL-mode database can catch it mid-write. The
    online backup API must not.
    """
    with app.app_context():
        from app.db import get_db
        get_db().execute("SELECT 1")  # hold a connection open
        path = create_backup(app.config["DATABASE"], tmp_path)

    assert verify_backup(path) is True


def test_a_truncated_backup_fails_verification(tmp_path):
    broken = tmp_path / "golden-finds_broken.db"
    broken.write_bytes(b"this is not a database")

    with pytest.raises(BackupError):
        verify_backup(broken)


def test_an_empty_file_fails_verification(tmp_path):
    empty = tmp_path / "golden-finds_empty.db"
    empty.touch()

    with pytest.raises(BackupError, match="missing or empty"):
        verify_backup(empty)


def test_a_valid_but_wrong_database_fails_verification(tmp_path):
    """Structurally fine, but not this application's database."""
    other = tmp_path / "golden-finds_other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE something (id INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(BackupError, match="missing tables"):
        verify_backup(other)


def test_missing_database_is_reported(tmp_path):
    with pytest.raises(BackupError, match="No database at"):
        create_backup(tmp_path / "nope.db", tmp_path)


def test_old_backups_are_pruned(app, stocked, tmp_path):
    import os
    import time

    # Six backups, aged a day apart. create_backup names by the minute, so
    # they are written directly here to get six distinct, ordered files.
    real = create_backup(app.config["DATABASE"], tmp_path)
    now = time.time()
    for index in range(1, 6):
        copy = tmp_path / f"golden-finds_2026-01-0{index}_1200.db"
        copy.write_bytes(real.read_bytes())
        age = now - index * 86400
        os.utime(copy, (age, age))

    assert len(list_backups(tmp_path)) == 6
    prune_backups(tmp_path, keep=3)

    remaining = list_backups(tmp_path)
    assert len(remaining) == 3
    assert remaining[0]["path"] == real, "the newest was kept"


def _close_app_connections(app):
    """
    Releases the app's handle on the database file.

    Windows refuses to rename a file that another handle holds open, and
    restore_backup deliberately reports that rather than corrupting a
    running app - so a test that restores has to close up first, exactly
    as the operator would stop the app first.
    """
    from app.db import close_db

    # Called without pushing a new context on purpose: the connection to
    # close belongs to the context the fixtures already pushed, and a new
    # one would have its own empty `g`.
    close_db()


def test_restore_brings_the_data_back(app, stocked, tmp_path):
    """The test that makes the backup real: restore it and check."""
    backup = create_backup(app.config["DATABASE"], tmp_path)

    # Trade on after the backup, then lose it all.
    with app.app_context():
        with transaction() as conn:
            conn.execute("DELETE FROM payments")
            conn.execute("DELETE FROM stock_movements")
            conn.execute("DELETE FROM sale_items")
            conn.execute("DELETE FROM sales")
            conn.execute("UPDATE products SET stock_quantity = 0")

        assert query_one("SELECT COUNT(*) AS n FROM sales")["n"] == 0

    _close_app_connections(app)
    restore_backup(backup, app.config["DATABASE"])

    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM sales")["n"] == 1
        assert query_one(
            "SELECT stock_quantity FROM products WHERE name = 'Sugar 1kg'"
        )["stock_quantity"] == 26


def test_restore_keeps_the_database_it_replaced(app, stocked, tmp_path):
    """Restoring the wrong file must itself be recoverable."""
    backup = create_backup(app.config["DATABASE"], tmp_path)

    _close_app_connections(app)
    restore_backup(backup, app.config["DATABASE"])

    kept = list(Path(app.config["DATABASE"]).parent.glob("*before-restore*"))
    assert kept, "the replaced database was set aside, not destroyed"


def test_restore_is_refused_while_the_app_holds_the_database(app, stocked, tmp_path):
    """
    Restoring underneath a running app would corrupt what it is holding,
    so it must fail loudly rather than half-succeed.
    """
    backup = create_backup(app.config["DATABASE"], tmp_path)

    with app.app_context():
        from app.db import get_db
        get_db().execute("SELECT 1")  # hold the file open

        with pytest.raises(BackupError, match="in use"):
            restore_backup(backup, app.config["DATABASE"])


def test_restoring_a_broken_file_is_refused(app, stocked, tmp_path):
    broken = tmp_path / "golden-finds_broken.db"
    broken.write_bytes(b"rubbish")

    with pytest.raises(BackupError):
        restore_backup(broken, app.config["DATABASE"])

    # And the live database is untouched.
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM sales")["n"] == 1


def test_age_is_none_when_nothing_has_been_backed_up(tmp_path):
    assert last_backup_age(tmp_path) is None


def test_age_is_small_right_after_a_backup(app, stocked, tmp_path):
    create_backup(app.config["DATABASE"], tmp_path)
    assert last_backup_age(tmp_path).total_seconds() < 60
