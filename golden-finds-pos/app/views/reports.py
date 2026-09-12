"""
Reports. Owner-only - these are the shop's takings and margins.
"""

import csv
import io
from datetime import date, timedelta

from flask import Blueprint, Response, render_template, request

from ..money import format_money
from ..security import admin_required
from ..services import reports as rpt

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _requested_day():
    raw = request.args.get("date")
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


@bp.get("/")
@admin_required
def index():
    day = _requested_day()
    return render_template(
        "reports/daily.html",
        day=day,
        yesterday=day - timedelta(days=1),
        tomorrow=day + timedelta(days=1),
        is_today=day == date.today(),
        summary=rpt.daily_summary(day),
        by_cashier=rpt.sales_by_cashier(day),
        top=rpt.top_products(date_from=day, date_to=day, limit=10),
    )


@bp.get("/products")
@admin_required
def product_performance():
    date_to = _requested_day()
    days = request.args.get("days", type=int) or 30
    date_from = date_to - timedelta(days=days - 1)

    return render_template(
        "reports/products.html",
        date_from=date_from,
        date_to=date_to,
        days=days,
        top=rpt.top_products(date_from=date_from, date_to=date_to, limit=50),
        dead=rpt.dead_stock(days=60, limit=25),
        inventory=rpt.inventory_value(),
    )


@bp.get("/daily.csv")
@admin_required
def daily_csv():
    """
    The day's close as a CSV, so the owner can keep it, mail it to an
    accountant, or open it in a spreadsheet without needing this app.
    """
    day = _requested_day()
    summary = rpt.daily_summary(day)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Golden Finds - daily summary", str(day)])
    writer.writerow([])
    writer.writerow(["Measure", "Amount (KSh)"])

    for label, key in (
        ("Sales", "sale_count"),
        ("Units sold", "units_sold"),
        ("Gross revenue", "revenue_cents"),
        ("Refunds", "refunded_cents"),
        ("Net revenue", "net_revenue_cents"),
        ("Cost of goods", "cost_cents"),
        ("Profit", "profit_cents"),
        ("Cash", "cash_cents"),
        ("M-Pesa", "mpesa_cents"),
        ("Voided sales", "void_count"),
    ):
        value = summary[key]
        writer.writerow([
            label,
            format_money(value, symbol=False) if key.endswith("_cents") else value,
        ])

    writer.writerow([])
    writer.writerow(["Cashier", "Sales", "Revenue (KSh)"])
    for row in rpt.sales_by_cashier(day):
        writer.writerow([
            row["name"], row["sale_count"],
            format_money(row["revenue_cents"], symbol=False),
        ])

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="golden-finds-{day}.csv"'
        },
    )
