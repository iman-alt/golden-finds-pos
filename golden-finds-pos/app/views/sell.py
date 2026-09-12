"""
The till: sell screen, receipts, sales history, voids and returns.
"""

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for
)

from ..db import transaction
from ..security import admin_required, current_user, login_required
from ..services import offers, sales, users
from ..services.sales import SaleError

bp = Blueprint("sell", __name__)


@bp.get("/sell")
@login_required
def screen():
    """
    The till.

    Whoever is standing here also gets told what is about to expire.
    Both roles see it, because the person actually handing goods over is
    the one who can say "this one is on offer today" - but only the owner
    gets the button that sets the price.
    """
    alerts = [
        alert for alert in offers.get_expiry_alerts()
        if alert["tier"] in ("urgent", "consider_offer", "expired")
    ]
    return render_template(
        "sell.html",
        expiring=alerts,
        is_admin=current_user()["role"] == "admin",
    )


@bp.get("/receipt/<int:sale_id>")
@login_required
def receipt(sale_id):
    sale = sales.get_sale(sale_id)
    if sale is None:
        abort(404)
    return render_template(
        "receipt.html",
        sale=sale,
        items=sales.get_sale_items(sale_id),
        # Printed straight from the browser; the stylesheet strips the
        # page chrome so a thermal printer gets just the receipt.
        print_mode=request.args.get("print") == "1",
    )


@bp.get("/sales")
@login_required
def history():
    from datetime import date

    user = current_user()
    is_admin = user["role"] == "admin"

    # The owner can look at any day and any cashier. A shopkeeper sees only
    # their own sales, and only today's - the query ignores whatever dates
    # or cashier are put in the address bar.
    cashier_id = request.args.get("cashier_id", type=int)
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    if not is_admin:
        cashier_id = user["id"]
        date_from = date_to = date.today().isoformat()

    return render_template(
        "sales_history.html",
        today=date.today(),
        sales=sales.list_sales(
            date_from=date_from,
            date_to=date_to,
            cashier_id=cashier_id,
            search=request.args.get("q") or None,
            limit=100,
        ),
        cashiers=users.list_users() if user["role"] == "admin" else [],
        filters=request.args,
        is_admin=user["role"] == "admin",
    )


@bp.get("/my-day")
@login_required
def my_day():
    """
    A shopkeeper's own handover sheet: what they sold today and what
    should be in the drawer. No costs, no margins - the owner's figures
    stay the owner's.
    """
    from datetime import date

    from ..services import reports

    user = current_user()
    try:
        day = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        day = date.today()

    return render_template(
        "my_day.html",
        day=day,
        is_today=day == date.today(),
        summary=reports.cashier_day(user["id"], day),
        sales=sales.list_sales(date_from=day, date_to=day,
                               cashier_id=user["id"], limit=100),
        expiring=offers.get_expiry_alerts(),
    )


@bp.post("/sales/<int:sale_id>/void")
@admin_required
def void(sale_id):
    """
    Voiding reverses money and stock, so it is the owner's to do - a
    cashier who can void their own sales can take cash out of the drawer
    and erase the evidence.
    """
    try:
        with transaction() as conn:
            sales.void_sale(
                conn, sale_id,
                voided_by=current_user()["id"],
                reason=request.form.get("reason", ""),
            )
    except SaleError as err:
        flash(str(err), "error")
        return redirect(url_for("sell.receipt", sale_id=sale_id))

    flash("Sale voided and stock returned.", "success")
    return redirect(url_for("sell.receipt", sale_id=sale_id))


@bp.route("/sales/<int:sale_id>/return", methods=["GET", "POST"])
@admin_required
def refund(sale_id):
    sale = sales.get_sale(sale_id)
    if sale is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "return.html", sale=sale, items=sales.get_sale_items(sale_id)
        )

    try:
        with transaction() as conn:
            refunded = sales.record_return(
                conn,
                sale_id=sale_id,
                product_id=request.form.get("product_id", type=int),
                quantity=request.form.get("quantity", type=int) or 0,
                reason=request.form.get("reason", "").strip(),
                restocked=request.form.get("restocked") == "yes",
                created_by=current_user()["id"],
            )
    except SaleError as err:
        flash(str(err), "error")
        return redirect(url_for("sell.refund", sale_id=sale_id))

    flash(f"Refunded {refunded / 100:,.2f}.", "success")
    return redirect(url_for("sell.receipt", sale_id=sale_id))
