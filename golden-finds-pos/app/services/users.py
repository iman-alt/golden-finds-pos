"""
Staff accounts.
"""

from ..db import audit, query_all, query_one
from ..security import AuthError, hash_pin


def get(user_id):
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def list_users(include_inactive=True):
    clause = "" if include_inactive else "WHERE active = 1"
    return query_all(
        f"""
        SELECT id, name, role, active, created_at, last_login
        FROM users {clause}
        ORDER BY active DESC, role, name
        """
    )


def count_admins(conn=None):
    execute = conn.execute if conn is not None else None
    sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1"
    row = execute(sql).fetchone() if execute else query_one(sql)
    return row["n"]


def create(conn, *, name, pin, role, created_by=None):
    name = (name or "").strip()
    if not name:
        raise AuthError("Name is required.")
    if role not in ("admin", "cashier"):
        raise AuthError("Role must be admin or cashier.")

    existing = conn.execute(
        "SELECT id FROM users WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if existing:
        raise AuthError("Someone already uses that name.")

    cursor = conn.execute(
        "INSERT INTO users (name, pin_hash, role) VALUES (?, ?, ?)",
        (name, hash_pin(pin), role),
    )
    user_id = cursor.lastrowid
    audit(conn, created_by, "user_created", "user", user_id, f"{name} ({role})")
    return user_id


def change_pin(conn, user_id, new_pin, *, changed_by):
    conn.execute(
        "UPDATE users SET pin_hash = ? WHERE id = ?", (hash_pin(new_pin), user_id)
    )
    audit(conn, changed_by, "pin_changed", "user", user_id)


def set_active(conn, user_id, active, *, changed_by):
    """
    Deactivates or restores an account.

    Accounts are never deleted - sales, stock movements and audit entries
    point at them, and a shop needs to be able to answer "who sold this"
    about someone who left two years ago.
    """
    user = get(user_id)
    if user is None:
        raise AuthError("User not found.")

    if not active and user["role"] == "admin" and count_admins(conn) <= 1:
        raise AuthError("You cannot deactivate the last owner account.")
    if not active and user_id == changed_by:
        raise AuthError("You cannot deactivate your own account.")

    conn.execute(
        "UPDATE users SET active = ? WHERE id = ?", (int(bool(active)), user_id)
    )
    audit(conn, changed_by, "user_activated" if active else "user_deactivated",
          "user", user_id, user["name"])


def set_role(conn, user_id, role, *, changed_by):
    if role not in ("admin", "cashier"):
        raise AuthError("Role must be admin or cashier.")
    user = get(user_id)
    if user is None:
        raise AuthError("User not found.")
    if user["role"] == "admin" and role != "admin" and count_admins(conn) <= 1:
        raise AuthError("You cannot demote the last owner account.")

    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    audit(conn, changed_by, "role_changed", "user", user_id,
          f"{user['role']} -> {role}")
