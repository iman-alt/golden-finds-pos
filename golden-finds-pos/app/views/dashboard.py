"""
The dashboard - what needs attention today.
"""

from flask import Blueprint, render_template

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
        })

    return render_template("dashboard.html", **context)
