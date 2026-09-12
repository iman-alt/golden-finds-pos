"""
Expiry monitoring and offer pricing.

The rule this module exists to enforce: the system never discounts
anything by itself. It watches expiry dates and tells the owner which
batches are becoming a problem; a human then chooses a price and approves
it. Every offer therefore carries the id of the person who approved it.
"""

from datetime import date, datetime

from ..db import audit, query_all, query_one

# Days-until-expiry thresholds, most urgent first. The first tier whose
# limit the batch falls within is the one it gets.
TIERS = (
    ("urgent", 3),
    ("consider_offer", 7),
    ("warning", 14),
    ("monitor", 30),
)

TIER_LABELS = {
    "expired": "Expired",
    "urgent": "Urgent",
    "consider_offer": "Consider offer",
    "warning": "Warning",
    "monitor": "Monitor",
}

# Suggested discounts per tier. These are only suggestions rendered as
# buttons for the owner to pick from - nothing applies them automatically.
TIER_SUGGESTED_DISCOUNTS = {
    "expired": (),
    "urgent": (30, 40, 50),
    "consider_offer": (20, 25, 30),
    "warning": (10, 15, 20),
    "monitor": (5, 10),
}


def tier_for_days_left(days_left):
    """Maps days-until-expiry to a tier, or None if it is not yet a concern."""
    if days_left < 0:
        return "expired"
    for name, limit in TIERS:
        if days_left <= limit:
            return name
    return None


def get_expiry_alerts(today=None):
    """
    Every batch with stock left that is within 30 days of expiry, or past
    it, soonest first. Batches that already have an active offer are
    marked so the dashboard does not invite the owner to discount twice.
    """
    today = today or date.today()

    batches = query_all(
        """
        SELECT b.id                AS batch_id,
               b.product_id,
               b.quantity_remaining,
               b.expiry_date,
               p.name              AS product_name,
               p.retail_price_cents,
               o.id                AS offer_id,
               o.offer_price_cents
        FROM batches b
        JOIN products p ON p.id = b.product_id
        LEFT JOIN offers o
               ON o.batch_id = b.id
              AND o.active = 1
              AND (o.end_date IS NULL OR o.end_date > datetime('now'))
        WHERE b.quantity_remaining > 0
          AND b.expiry_date IS NOT NULL
          AND p.active = 1
        ORDER BY b.expiry_date ASC
        """
    )

    alerts = []
    for batch in batches:
        expiry = date.fromisoformat(batch["expiry_date"])
        days_left = (expiry - today).days
        tier = tier_for_days_left(days_left)
        if tier is None:
            continue

        retail = batch["retail_price_cents"]
        alerts.append({
            "batch_id": batch["batch_id"],
            "product_id": batch["product_id"],
            "product_name": batch["product_name"],
            "retail_price_cents": retail,
            "quantity_remaining": batch["quantity_remaining"],
            "expiry_date": batch["expiry_date"],
            "days_left": days_left,
            "tier": tier,
            "tier_label": TIER_LABELS[tier],
            "has_offer": batch["offer_id"] is not None,
            "offer_price_cents": batch["offer_price_cents"],
            # Value still sitting on the shelf, which is what makes the
            # owner care about this row at all.
            "value_at_risk_cents": retail * batch["quantity_remaining"],
            "suggestions": [
                {
                    "percent": pct,
                    # Round suggestions to the shilling; nobody prices
                    # shelf stock at KSh 47.60.
                    "price_cents": max(100, round(retail * (100 - pct) / 100 / 100) * 100),
                }
                for pct in TIER_SUGGESTED_DISCOUNTS[tier]
            ],
        })
    return alerts


def get_active_offer(product_id, batch_id=None):
    """
    The offer that currently applies to a product, or None.

    Unlike the original version, this honours end_date - an offer that has
    run out stops applying on its own rather than living forever. A
    batch-specific offer is preferred over a product-wide one.
    """
    return query_one(
        """
        SELECT * FROM offers
        WHERE product_id = ?
          AND active = 1
          AND start_date <= datetime('now')
          AND (end_date IS NULL OR end_date > datetime('now'))
          AND (batch_id IS NULL OR batch_id = COALESCE(?, batch_id))
        ORDER BY (batch_id IS NOT NULL) DESC, start_date DESC
        LIMIT 1
        """,
        (product_id, batch_id),
    )


def create_offer(conn, *, product_id, offer_price_cents, approved_by,
                 batch_id=None, tier=None, end_date=None):
    """
    Records an admin-approved offer price.

    Refuses a price above retail (that is a price rise, not an offer) and
    ends any existing offer on the same product first, so two overlapping
    offers can never leave the applied price ambiguous.
    """
    product = conn.execute(
        "SELECT * FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        raise ValueError("Product not found.")

    if offer_price_cents <= 0:
        raise ValueError("Offer price must be greater than zero.")
    if offer_price_cents >= product["retail_price_cents"]:
        raise ValueError("An offer price must be below the retail price.")

    if end_date:
        try:
            datetime.fromisoformat(str(end_date))
        except ValueError:
            raise ValueError("Offer end date is not a valid date.")

    # Supersede whatever was running, so lookup is never ambiguous.
    conn.execute(
        """
        UPDATE offers SET active = 0, ended_by = ?, ended_at = datetime('now')
        WHERE product_id = ? AND active = 1
        """,
        (approved_by, product_id),
    )

    cursor = conn.execute(
        """
        INSERT INTO offers (product_id, batch_id, offer_price_cents, tier,
                            approved_by, end_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (product_id, batch_id, offer_price_cents, tier, approved_by, end_date),
    )
    offer_id = cursor.lastrowid

    audit(
        conn, approved_by, "offer_created", "offer", offer_id,
        f"product={product_id} batch={batch_id} "
        f"price={offer_price_cents} was={product['retail_price_cents']}",
    )
    return offer_id


def end_offer(conn, offer_id, ended_by):
    """Stops an offer early."""
    conn.execute(
        """
        UPDATE offers SET active = 0, ended_by = ?, ended_at = datetime('now')
        WHERE id = ? AND active = 1
        """,
        (ended_by, offer_id),
    )
    audit(conn, ended_by, "offer_ended", "offer", offer_id)


def list_active_offers():
    return query_all(
        """
        SELECT o.*, p.name AS product_name, p.retail_price_cents,
               u.name AS approved_by_name
        FROM offers o
        JOIN products p ON p.id = o.product_id
        LEFT JOIN users u ON u.id = o.approved_by
        WHERE o.active = 1
          AND (o.end_date IS NULL OR o.end_date > datetime('now'))
        ORDER BY o.start_date DESC
        """
    )
