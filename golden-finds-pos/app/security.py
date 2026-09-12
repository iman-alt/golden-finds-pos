"""
Authentication, authorisation and CSRF.

The shop signs in with a name and a PIN, because a cashier at a counter
is not going to type a password between customers. A PIN is weak by
nature, so it is defended in depth: hashed with a slow KDF, never logged,
and rate-limited per user so the short keyspace cannot simply be walked.
"""

import hmac
import secrets
import time
from functools import wraps

from flask import (
    current_app, flash, g, jsonify, redirect, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import audit, query_one, transaction

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 12

# user_id -> (failed_attempt_count, locked_until_timestamp)
# In-process and deliberately so: this is one till on one machine, and a
# restart clearing the counter is an acceptable trade for having no extra
# moving parts. If this ever runs multi-process, move it to the database.
_attempts = {}


class AuthError(Exception):
    """Raised for a sign-in that cannot proceed. Message is user-safe."""


# ------------------------------------------------------------------ PINs --

def hash_pin(pin):
    """
    Hashes a PIN with scrypt. Deliberately slow, so that even a 4-digit
    PIN costs real time per guess rather than being instantly enumerable.
    """
    validate_pin(pin)
    return generate_password_hash(pin, method="scrypt")


def validate_pin(pin):
    if not pin or not pin.isdigit():
        raise AuthError("PIN must be digits only.")
    if not PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH:
        raise AuthError(f"PIN must be {PIN_MIN_LENGTH}-{PIN_MAX_LENGTH} digits.")
    if len(set(pin)) == 1:
        raise AuthError("PIN cannot be the same digit repeated.")
    if pin in ("1234", "0000", "1111", "123456"):
        raise AuthError("That PIN is too common. Choose another.")
    return pin


def _lockout_remaining(user_id):
    record = _attempts.get(user_id)
    if not record:
        return 0
    _, locked_until = record
    return max(0, int(locked_until - time.time()))


def _record_failure(user_id):
    count, _ = _attempts.get(user_id, (0, 0))
    count += 1
    locked_until = 0
    if count >= current_app.config["MAX_PIN_ATTEMPTS"]:
        locked_until = time.time() + current_app.config["LOCKOUT_SECONDS"]
        count = 0
    _attempts[user_id] = (count, locked_until)


def _clear_failures(user_id):
    _attempts.pop(user_id, None)


def reset_attempts():
    """
    Clears every lockout. Used by the tests, which reuse user ids across
    fresh databases, and available to an owner who needs to unlock a
    cashier without waiting out the timer.
    """
    _attempts.clear()


def authenticate(name, pin):
    """
    Verifies a name and PIN, returning the user row.

    Raises AuthError with a deliberately vague message on failure - saying
    "no such user" would tell someone which names are worth attacking.
    """
    name = (name or "").strip()
    if not name or not pin:
        raise AuthError("Enter your name and PIN.")

    user = query_one(
        "SELECT * FROM users WHERE name = ? COLLATE NOCASE", (name,)
    )

    if user is None:
        # Spend roughly the same time as a real check so that a missing
        # user is not distinguishable from a wrong PIN by timing alone.
        check_password_hash(
            generate_password_hash("0" * 6, method="scrypt"), "000000"
        )
        raise AuthError("Name or PIN is incorrect.")

    remaining = _lockout_remaining(user["id"])
    if remaining:
        raise AuthError(
            f"Too many wrong PINs. Try again in {remaining // 60 + 1} minute(s)."
        )

    if not user["active"]:
        raise AuthError("That account has been deactivated. See the owner.")

    if not check_password_hash(user["pin_hash"], pin):
        _record_failure(user["id"])
        with transaction() as conn:
            audit(conn, user["id"], "login_failed", "user", user["id"])
        raise AuthError("Name or PIN is incorrect.")

    _clear_failures(user["id"])
    return user


def login_user(user):
    """Starts a session. Rotates the session id to prevent fixation."""
    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["name"] = user["name"]
    session.permanent = True

    with transaction() as conn:
        conn.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?",
            (user["id"],),
        )
        audit(conn, user["id"], "login", "user", user["id"])


def logout_user():
    user_id = session.get("user_id")
    if user_id:
        with transaction() as conn:
            audit(conn, user_id, "logout", "user", user_id)
    session.clear()


def current_user():
    """
    The signed-in user row, or None. Cached on `g` so that the several
    places per request that ask do not each hit the database.
    """
    if "user" not in g:
        user_id = session.get("user_id")
        g.user = (
            query_one("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,))
            if user_id else None
        )
        # Account deactivated mid-session: drop the session immediately.
        if user_id and g.user is None:
            session.clear()
    return g.user


# ------------------------------------------------------------ DECORATORS --

def _wants_json():
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best == "application/json"
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if _wants_json():
                return jsonify({"success": False, "message": "Please sign in."}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            if _wants_json():
                return jsonify({"success": False, "message": "Please sign in."}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        if user["role"] != "admin":
            if _wants_json():
                return jsonify(
                    {"success": False, "message": "Only the owner can do that."}
                ), 403
            flash("Only the owner can do that.", "error")
            return redirect(url_for("dashboard.index")), 403
        return view(*args, **kwargs)
    return wrapped


# ----------------------------------------------------------------- CSRF --

def csrf_token():
    """The per-session CSRF token, created on first use."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def verify_csrf():
    """
    Checks the token on every state-changing request.

    Forms send it in a hidden field, fetch() sends it in X-CSRF-Token.
    Compared with compare_digest so the check itself leaks nothing.
    """
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return True
    if current_app.config.get("TESTING") and not current_app.config.get("CSRF_IN_TESTS"):
        return True

    sent = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or (request.get_json(silent=True) or {}).get("csrf_token")
        or ""
    )
    expected = session.get("csrf_token", "")
    return bool(expected) and hmac.compare_digest(str(sent), str(expected))
