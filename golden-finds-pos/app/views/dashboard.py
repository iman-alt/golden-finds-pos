"""
The dashboard - what needs attention today.
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, render_template

from ..backup import last_backup_age
from ..security import current_user, login_required
from ..services import offers, reports, stock
from ..services.icons import icon_for

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    user = current_user()
    is_admin = user["role"] == "admin"

    alerts = offers.get_expiry_alerts()
    for alert in alerts:
        alert["icon"] = icon_for(alert["product_name"])

    low_stock = [
        dict(row, icon=icon_for(row["name"], row["category"]))
        for row in stock.get_low_stock(limit=12)
    ]

    context = {
        "greeting": _greeting(),
        "today_label": date.today().strftime("%A, %d %B"),
        "low_stock": low_stock,
        "expiry_alerts": alerts,
        "expiry_count": len(alerts),
        "urgent_count": sum(
            1 for a in alerts if a["tier"] in ("urgent", "expired")
        ),
        "is_admin": is_admin,
    }

    # A shopkeeper gets their own shift and the operational half of this
    # screen. Profit and what the stock is worth are the owner's business.
    if is_admin:
        context.update({
            "today": reports.daily_summary(),
            "week": _week(reports.revenue_series(days=7)),
            "inventory": reports.inventory_value(),
            "discrepancies": stock.find_discrepancies(),
            "backup_warning": _backup_warning(),
        })
    else:
        context["shift"] = reports.cashier_day(user["id"])

    return render_template("dashboard.html", **context)


def _greeting():
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _week(series):
    """
    The last seven days, oldest first, with quiet days shown as zero
    rather than missing - a gap in the chart would read as missing data.
    """
    by_day = {row["day"]: row["revenue_cents"] for row in series}
    days = []
    for offset in range(6, -1, -1):
        day = date.today() - timedelta(days=offset)
        days.append({
            "label": "Today" if offset == 0 else day.strftime("%a"),
            "date": day.isoformat(),
            "revenue_cents": by_day.get(day.isoformat(), 0),
            "is_today": offset == 0,
        })
    peak = max([d["revenue_cents"] for d in days] + [1])
    for d in days:
        d["pct"] = round(d["revenue_cents"] / peak * 100)
    return days


def _backup_warning():
    """
    Says so plainly when the backups have gone stale or never started.
    Everything the shop knows is in one file; the owner should not have to
    remember to go and check on it.
    """
    age = last_backup_age(current_app.config["BACKUP_DIR"])
    if age is None:
        return "No backup has ever been taken of this shop's data."

    hours = age.total_seconds() / 3600
    if hours > current_app.config["BACKUP_WARN_AFTER_HOURS"]:
        days = int(hours // 24)
        return (
            f"The last backup was {days} day{'' if days == 1 else 's'} ago."
            if days else f"The last backup was {int(hours)} hours ago."
        )
    return None
