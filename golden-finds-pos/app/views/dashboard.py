"""
The dashboard - what needs attention today.
"""

from flask import Blueprint, current_app, render_template

from ..backup import last_backup_age
from ..security import current_user, login_required
from ..services import offers, reports, stock

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    user = current_user()
    is_admin = user["role"] == "admin"

    alerts = offers.get_expiry_alerts()

    context = {
        "low_stock": stock.get_low_stock(limit=12),
        "expiry_alerts": alerts,
        "expiry_count": len(alerts),
        "urgent_count": sum(
            1 for a in alerts if a["tier"] in ("urgent", "expired")
        ),
        "is_admin": is_admin,
    }

    # A cashier gets the operational half of this screen only. Takings,
    # margins and what the stock is worth are the owner's business.
    if is_admin:
        context.update({
            "today": reports.daily_summary(),
            "revenue_series": reports.revenue_series(days=14),
            "inventory": reports.inventory_value(),
            "discrepancies": stock.find_discrepancies(),
            "credit": reports.outstanding_credit(),
            "backup_warning": _backup_warning(),
        })

    return render_template("dashboard.html", **context)


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
