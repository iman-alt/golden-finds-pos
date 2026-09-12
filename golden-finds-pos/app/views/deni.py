"""
The deni book. Open to everyone who serves - giving goods on deni and
taking repayments are part of selling. Cancelling a deni is the owner's.
"""

from datetime import date

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for
)

from ..db import transaction
from ..money import MoneyError, format_money, parse_money
from ..security import admin_required, current_user, login_required
from ..services import deni
from ..services.deni import DeniError, format_phone, normalise_phone
from ..services.stock import StockError

bp = Blueprint("deni", __name__, url_prefix="/deni")


@bp.app_template_filter("phone")
def _phone_filter(value):
    return format_phone(value)


@bp.app_template_filter("days_since")
def _days_since(value):
    try:
        return (date.today() - date.fromisoformat(str(value)[:10])).days
    except ValueError:
        return 0


def _page(form=None, error=None, status=200):
    show = request.args.get("show", "open")
    people = deni.debtors()
    return render_template(
        "deni.html",
        summary=deni.summary(),
        debtors=people,
        entries=deni.entries(
            search=request.args.get("q", "").strip() or None,
            status=None if show == "all" else "open",
        ),
        debtor_data=[
            {"phone": p["phone"], "name": p["customer_name"],
             "owed": format_money(p["owed_cents"])}
            for p in people
        ],
        filters=request.args,
        show=show,
        form=form or {},
        error=error,
        today=date.today().isoformat(),
        is_admin=current_user()["role"] == "admin",
    ), status


@bp.get("/")
@login_required
def index():
    return _page()


@bp.post("/")
@login_required
def create():
    form = request.form
    try:
        with transaction() as conn:
            deni.record(
                conn,
                customer_name=form.get("customer_name"),
                phone=form.get("phone"),
                product_id=form.get("product_id"),
                quantity=form.get("quantity"),
                taken_on=form.get("taken_on") or None,
                created_by=current_user()["id"],
            )
    except (DeniError, StockError) as err:
        return _page(form=form, error=str(err), status=400)

    flash(f"Deni recorded for {' '.join(form.get('customer_name', '').split())}.", "success")
    return redirect(url_for("deni.index"))


@bp.get("/person/<phone>")
@login_required
def person(phone):
    try:
        phone = normalise_phone(phone)
    except DeniError:
        abort(404)

    items = deni.entries(phone=phone, limit=500)
    if not items:
        abort(404)

    owed = sum(i["total_cents"] - i["paid_cents"] for i in items if i["status"] == "open")
    return render_template(
        "deni_person.html",
        phone=phone,
        name=items[0]["customer_name"],
        owed_cents=owed,
        items=items,
        payments=deni.payments(phone),
        is_admin=current_user()["role"] == "admin",
    )


@bp.post("/pay")
@login_required
def pay():
    phone = request.form.get("phone", "")
    back = request.form.get("back") or url_for("deni.index")
    try:
        amount = parse_money(request.form.get("amount"), field="Amount paid")
        with transaction() as conn:
            left = deni.record_payment(
                conn, phone=phone, amount_cents=amount,
                method=request.form.get("method", "cash"),
                received_by=current_user()["id"],
            )
    except (DeniError, MoneyError) as err:
        flash(str(err), "error")
        return redirect(back)

    if left:
        flash(f"Payment of {format_money(amount)} recorded. Still owes {format_money(left)}.", "success")
    else:
        flash(f"Payment of {format_money(amount)} recorded. All cleared! 🎉", "success")
    return redirect(back)


@bp.post("/<int:deni_id>/cancel")
@admin_required
def cancel(deni_id):
    try:
        with transaction() as conn:
            deni.cancel(conn, deni_id, cancelled_by=current_user()["id"],
                        reason=request.form.get("reason"))
    except DeniError as err:
        flash(str(err), "error")
        return redirect(request.referrer or url_for("deni.index"))

    flash("Deni cancelled and the item is back in stock.", "success")
    return redirect(request.referrer or url_for("deni.index"))
