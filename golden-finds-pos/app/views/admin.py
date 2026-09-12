"""
Owner-only administration: staff, offers, and the audit trail.
"""

from flask import (
    Blueprint, flash, redirect, render_template, request, url_for
)

from ..db import query_all, transaction
from ..security import AuthError, admin_required, current_user, validate_pin
from ..services import offers, users

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/staff")
@admin_required
def staff():
    return render_template("admin/staff.html", staff=users.list_users())


@bp.post("/staff")
@admin_required
def staff_create():
    try:
        pin = request.form.get("pin", "")
        validate_pin(pin)
        with transaction() as conn:
            users.create(
                conn,
                name=request.form.get("name", ""),
                pin=pin,
                role=request.form.get("role", "cashier"),
                created_by=current_user()["id"],
            )
    except AuthError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.staff"))

    flash("Staff member added.", "success")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:user_id>/active")
@admin_required
def staff_set_active(user_id):
    active = request.form.get("active") == "1"
    try:
        with transaction() as conn:
            users.set_active(conn, user_id, active, changed_by=current_user()["id"])
    except AuthError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.staff"))

    flash("Account reactivated." if active else "Account deactivated.", "success")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:user_id>/role")
@admin_required
def staff_set_role(user_id):
    try:
        with transaction() as conn:
            users.set_role(conn, user_id, request.form.get("role", "cashier"),
                           changed_by=current_user()["id"])
    except AuthError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.staff"))

    flash("Role updated.", "success")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:user_id>/reset-pin")
@admin_required
def staff_reset_pin(user_id):
    """
    For the cashier who has forgotten their PIN. The owner sets a new one
    directly; the audit log records that they did.
    """
    try:
        pin = request.form.get("pin", "")
        validate_pin(pin)
        with transaction() as conn:
            users.change_pin(conn, user_id, pin, changed_by=current_user()["id"])
    except AuthError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.staff"))

    flash("PIN reset. Tell them to change it after signing in.", "success")
    return redirect(url_for("admin.staff"))


@bp.get("/offers")
@admin_required
def offer_list():
    return render_template(
        "admin/offers.html",
        offers=offers.list_active_offers(),
        alerts=offers.get_expiry_alerts(),
    )


@bp.get("/pairings")
@admin_required
def pairing_list():
    """
    What sells together. A merchandising aid for the owner - which things
    to stand next to each other, and which to bundle.
    """
    from ..services import pairings, products

    days = request.args.get("days", type=int) or 90
    return render_template(
        "admin/pairings.html",
        days=days,
        discovered=pairings.discover(days=days, limit=25),
        pinned=pairings.list_pinned(),
        products=products.list_products(limit=500),
    )


@bp.post("/pairings")
@admin_required
def pairing_pin():
    from ..services import pairings

    try:
        with transaction() as conn:
            pairings.pin(
                conn,
                request.form.get("product_a"),
                request.form.get("product_b"),
                created_by=current_user()["id"],
                note=request.form.get("note", "").strip() or None,
            )
    except (ValueError, TypeError) as err:
        flash(str(err) or "Choose two different products.", "error")
        return redirect(url_for("admin.pairing_list"))

    flash("Pairing saved.", "success")
    return redirect(url_for("admin.pairing_list"))


@bp.post("/pairings/remove")
@admin_required
def pairing_unpin():
    from ..services import pairings

    with transaction() as conn:
        pairings.unpin(conn, request.form.get("product_a"),
                       request.form.get("product_b"),
                       removed_by=current_user()["id"])

    flash("Pairing removed.", "success")
    return redirect(url_for("admin.pairing_list"))


@bp.get("/audit")
@admin_required
def audit_log():
    """
    Everything sensitive that has happened, newest first. This is the
    screen that makes the rest of the system trustworthy - price changes,
    voids, PIN resets and failed logins are all here.
    """
    action = request.args.get("action") or None
    params = []
    clause = ""
    if action:
        clause = "WHERE a.action = ?"
        params.append(action)

    entries = query_all(
        f"""
        SELECT a.*, u.name AS user_name
        FROM audit_log a
        LEFT JOIN users u ON u.id = a.user_id
        {clause}
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 300
        """,
        params,
    )
    actions = query_all(
        "SELECT DISTINCT action FROM audit_log ORDER BY action"
    )
    return render_template(
        "admin/audit.html", entries=entries, actions=actions, selected=action
    )
