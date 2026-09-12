"""
Database access.

One connection per request, opened lazily and closed when the request ends.
Writes go through `transaction()`, which takes a real BEGIN IMMEDIATE lock -
without it two tills committing at the same moment can both read the same
stock level and both sell the last unit.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from flask import current_app, g

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Python 3.12 deprecated the implicit date adapters. Dates are stored as
# ISO text here - which is what SQLite's own date() functions expect - so
# the conversion is spelled out rather than left to a default that is
# going away.
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" ", timespec="seconds"))


def _configure(conn):
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard read while a sale is being written.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait rather than fail instantly if another write is in flight.
    conn.execute("PRAGMA busy_timeout = 5000")
    # Durable enough for a single-shop machine, much faster than FULL.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db():
    """The connection for the current request, opened on first use."""
    if "db" not in g:
        g.db = _configure(sqlite3.connect(
            current_app.config["DATABASE"],
            # We manage transactions explicitly in transaction().
            isolation_level=None,
        ))
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction():
    """
    Wraps a unit of work that writes.

    BEGIN IMMEDIATE grabs the write lock up front, so a concurrent sale
    blocks here instead of silently reading stale stock. Commits on clean
    exit, rolls back on any exception, and re-raises.

        with transaction() as conn:
            conn.execute(...)
    """
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query_all(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def query_one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def init_db():
    """
    Creates any missing tables. Safe to run on every start - every
    statement is CREATE ... IF NOT EXISTS, so existing data is untouched.
    """
    get_db().executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def audit(conn, user_id, action, entity_type=None, entity_id=None, detail=None):
    """
    Records a sensitive action. Takes the connection rather than opening
    its own, so the log entry commits or rolls back with the thing it
    describes - an audit row for a sale that failed would be a lie.
    """
    conn.execute(
        """
        INSERT INTO audit_log (user_id, action, entity_type, entity_id, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, action, entity_type, entity_id, detail),
    )


def get_setting(key, default=None):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
