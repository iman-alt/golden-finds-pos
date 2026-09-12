"""
Sign in, sign out, and first-run setup.
"""

from urllib.parse import urlparse

from flask import (
    Blueprint, flash, redirect, render_template, request, session, url_for
)

from ..db import query_one, transaction
from ..security import (
    AuthError, authenticate, current_user, login_required, login_user,
    logout_user, validate_pin,
)
from ..services import users

bp = Blueprint("auth", __name__)


def _safe_next(target):
    """
    Only ever redirect within this site. An open redirect on a login page
    is how a staff member ends up typing their PIN into a copy of it.
    """
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target if target.startswith("/") else None


def _has_users():
    return query_one("SELECT 1 FROM users LIMIT 1") is not None


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """
    First run only. Creates the owner account, then closes itself off -
    once any user exists this route is no longer reachable, so it cannot
    be used later to mint a second owner.
    """
    if _has_users():
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("auth/setup.html")

    name = request.form.get("name", "").strip()
    pin = request.form.get("pin", "")
    confirm = request.form.get("confirm_pin", "")

    try:
        if pin != confirm:
            raise AuthError("The two PINs do not match.")
        validate_pin(pin)
        with transaction() as conn:
            users.create(conn, name=name, pin=pin, role="admin")
    except AuthError as err:
        return render_template("auth/setup.html", error=str(err), name=name)

    flash("Owner account created. Sign in to start.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not _has_users():
        return redirect(url_for("auth.setup"))
    if current_user() is not None:
        return redirect(url_for("dashboard.index"))

    if request.method == "GET":
        return render_template(
            "auth/login.html", names=users.list_users(include_inactive=False)
        )

    name = request.form.get("name", "")
    pin = request.form.get("pin", "")

    try:
        user = authenticate(name, pin)
    except AuthError as err:
        return render_template(
            "auth/login.html", error=str(err), name=name,
            names=users.list_users(include_inactive=False),
        ), 401

    login_user(user)
    destination = _safe_next(request.form.get("next") or request.args.get("next"))
    return redirect(destination or url_for("dashboard.index"))


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/change-pin", methods=["GET", "POST"])
@login_required
def change_pin():
    user = current_user()

    if request.method == "GET":
        return render_template("auth/change_pin.html")

    current = request.form.get("current_pin", "")
    new = request.form.get("new_pin", "")
    confirm = request.form.get("confirm_pin", "")

    try:
        authenticate(user["name"], current)
        if new != confirm:
            raise AuthError("The two new PINs do not match.")
        if new == current:
            raise AuthError("The new PIN must be different from the old one.")
        validate_pin(new)
        with transaction() as conn:
            users.change_pin(conn, user["id"], new, changed_by=user["id"])
    except AuthError as err:
        return render_template("auth/change_pin.html", error=str(err)), 400

    flash("PIN changed.", "success")
    return redirect(url_for("dashboard.index"))
