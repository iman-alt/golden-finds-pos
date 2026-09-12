"""
Reporting.

The old schema captured cost price on every product and every batch and
then never reported on it. This module is where that data comes back out:
what sold, what it cost, what was made, and who was on the till.

Voided sales are excluded from every figure here. They stay visible in
sales history and the audit log, but they are not takings.
"""

from datetime import date, timedelta

from ..db import query_all, query_one

_LIVE = "s.status = 'completed'"


def daily_summary(day=None):
    """
    The end-of-day close. This is the number the owner counts the drawer
    against, so it separates cash from M-Pesa from credit - only the cash
    line should match what is physically in the till.

    Profit is computed from the cost frozen onto each sale line at the
    time of sale, not from the product's current cost price, so changing a
    cost price tomorrow does not silently rewrite today's margin.
    """
    day = day or date.today()

    totals = query_one(
        f"""
        SELECT COUNT(*)                        AS sale_count,
               COALESCE(SUM(s.total_cents), 0) AS revenue_cents,
               COALESCE(SUM(CASE WHEN s.payment_method = 'cash'
                                 THEN s.total_cents ELSE 0 END), 0) AS cash_cents,
               COALESCE(SUM(CASE WHEN s.payment_method = 'mpesa'
                                 THEN s.total_cents ELSE 0 END), 0) AS mpesa_cents,
               COALESCE(SUM(CASE WHEN s.payment_method = 'credit'
                                 THEN s.total_cents ELSE 0 END), 0) AS credit_cents
        FROM sales s
        WHERE date(s.created_at) = date(?) AND {_LIVE}
        """,
        (day,),
    )

    goods = query_one(
        f"""
        SELECT COALESCE(SUM((si.quantity - si.quantity_returned)
                            * si.unit_cost_cents), 0)  AS cost_cents,
               COALESCE(SUM(si.quantity - si.quantity_returned), 0) AS units_sold
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        WHERE date(s.created_at) = date(?) AND {_LIVE}
        """,
        (day,),
    )

    refunds = query_one(
        """
        SELECT COALESCE(SUM(refunded_cents), 0) AS refunded_cents,
               COUNT(*)                         AS return_count
        FROM returns WHERE date(created_at) = date(?)
        """,
        (day,),
    )

    voids = query_one(
        """
        SELECT COUNT(*)                        AS void_count,
               COALESCE(SUM(total_cents), 0)   AS voided_cents
        FROM sales
        WHERE date(created_at) = date(?) AND status = 'voided'
        """,
        (day,),
    )

    net_revenue = totals["revenue_cents"] - refunds["refunded_cents"]

    return {
        "date": str(day),
        "sale_count": totals["sale_count"],
        "units_sold": goods["units_sold"],
        "revenue_cents": totals["revenue_cents"],
        "refunded_cents": refunds["refunded_cents"],
        "return_count": refunds["return_count"],
        "net_revenue_cents": net_revenue,
        "cost_cents": goods["cost_cents"],
        "profit_cents": net_revenue - goods["cost_cents"],
        "cash_cents": totals["cash_cents"],
        "mpesa_cents": totals["mpesa_cents"],
        "credit_cents": totals["credit_cents"],
        "void_count": voids["void_count"],
        "voided_cents": voids["voided_cents"],
        # What should physically be in the drawer, before any float.
        "expected_cash_cents": totals["cash_cents"],
    }


def sales_by_cashier(day=None):
    day = day or date.today()
    return query_all(
        f"""
        SELECT u.id, u.name,
               COUNT(s.id)                     AS sale_count,
               COALESCE(SUM(s.total_cents), 0) AS revenue_cents
        FROM sales s
        JOIN users u ON u.id = s.cashier_id
        WHERE date(s.created_at) = date(?) AND {_LIVE}
        GROUP BY u.id, u.name
        ORDER BY revenue_cents DESC
        """,
        (day,),
    )


def cashier_day(user_id, day=None):
    """
    One person's own day at the till.

    This is what a shopkeeper hands over on: how many sales they rang up
    and, separately, how much of it should physically be in the drawer.
    It deliberately shows no cost or margin - that is the owner's
    business, not the person selling.
    """
    day = day or date.today()

    totals = query_one(
        f"""
        SELECT COUNT(*)                        AS sale_count,
               COALESCE(SUM(s.total_cents), 0) AS revenue_cents,
               COALESCE(SUM(CASE WHEN s.payment_method = 'cash'
                                 THEN s.total_cents ELSE 0 END), 0) AS cash_cents,
               COALESCE(SUM(CASE WHEN s.payment_method = 'mpesa'
                                 THEN s.total_cents ELSE 0 END), 0) AS mpesa_cents,
               COALESCE(SUM(CASE WHEN s.payment_method = 'credit'
                                 THEN s.total_cents ELSE 0 END), 0) AS credit_cents
        FROM sales s
        WHERE s.cashier_id = ? AND date(s.created_at) = date(?) AND {_LIVE}
        """,
        (user_id, day),
    )

    units = query_one(
        f"""
        SELECT COALESCE(SUM(si.quantity - si.quantity_returned), 0) AS units
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        WHERE s.cashier_id = ? AND date(s.created_at) = date(?) AND {_LIVE}
        """,
        (user_id, day),
    )

    return {
        "date": str(day),
        "sale_count": totals["sale_count"],
        "units_sold": units["units"],
        "revenue_cents": totals["revenue_cents"],
        "cash_cents": totals["cash_cents"],
        "mpesa_cents": totals["mpesa_cents"],
        "credit_cents": totals["credit_cents"],
    }


def top_products(*, date_from=None, date_to=None, limit=10):
    date_to = date_to or date.today()
    date_from = date_from or (date.fromisoformat(str(date_to)) - timedelta(days=29))
    return query_all(
        f"""
        SELECT p.id, p.name, p.unit_type,
               SUM(si.quantity - si.quantity_returned)      AS units_sold,
               SUM(si.line_total_cents)                     AS revenue_cents,
               SUM((si.quantity - si.quantity_returned)
                   * (si.unit_price_cents - si.unit_cost_cents)) AS profit_cents
        FROM sale_items si
        JOIN sales s    ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE date(s.created_at) BETWEEN date(?) AND date(?) AND {_LIVE}
        GROUP BY p.id, p.name, p.unit_type
        HAVING units_sold > 0
        ORDER BY revenue_cents DESC
        LIMIT ?
        """,
        (date_from, date_to, limit),
    )


def dead_stock(days=60, limit=20):
    """
    Products with stock on the shelf that have not sold in `days`.
    This is money sitting still, and it is the report a small shop most
    often does not have.
    """
    return query_all(
        f"""
        SELECT p.id, p.name, p.stock_quantity, p.retail_price_cents,
               p.stock_quantity * p.retail_price_cents AS tied_up_cents,
               (SELECT MAX(s.created_at)
                FROM sale_items si JOIN sales s ON s.id = si.sale_id
                WHERE si.product_id = p.id AND {_LIVE}) AS last_sold
        FROM products p
        WHERE p.active = 1 AND p.stock_quantity > 0
          AND (last_sold IS NULL OR last_sold < datetime('now', ?))
        ORDER BY tied_up_cents DESC
        LIMIT ?
        """,
        (f"-{int(days)} days", limit),
    )


def revenue_series(days=14):
    """Daily revenue for the last `days` days, for the dashboard trend."""
    return query_all(
        f"""
        SELECT date(s.created_at)        AS day,
               SUM(s.total_cents)        AS revenue_cents,
               COUNT(*)                  AS sale_count
        FROM sales s
        WHERE s.created_at >= datetime('now', ?) AND {_LIVE}
        GROUP BY date(s.created_at)
        ORDER BY day
        """,
        (f"-{int(days)} days",),
    )


def inventory_value():
    """
    What the stock on hand is worth, at cost and at retail. The gap
    between the two is the margin still sitting on the shelves.
    """
    return query_one(
        """
        SELECT COALESCE(SUM(stock_quantity * cost_price_cents), 0)   AS cost_cents,
               COALESCE(SUM(stock_quantity * retail_price_cents), 0) AS retail_cents,
               COALESCE(SUM(stock_quantity), 0)                      AS units
        FROM products WHERE active = 1
        """
    )


def outstanding_credit():
    return query_all(
        """
        SELECT id, name, phone, credit_balance_cents
        FROM customers
        WHERE credit_balance_cents > 0
        ORDER BY credit_balance_cents DESC
        """
    )
