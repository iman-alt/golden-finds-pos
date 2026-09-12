"""
Stock movement.

`stock_movements` is the source of truth; `products.stock_quantity` and
`batches.quantity_remaining` are caches kept in step with it. Every
function here writes the ledger row in the same transaction as the cache
update, so the two can never disagree because of a crash.
"""

from ..db import audit, query_all, query_one


class StockError(Exception):
    """Raised when a stock operation cannot be completed. Message is user-safe."""


def _log(conn, *, product_id, batch_id, quantity_change, movement_type,
         reference_type=None, reference_id=None, note=None, created_by=None):
    conn.execute(
        """
        INSERT INTO stock_movements
            (product_id, batch_id, quantity_change, movement_type,
             reference_type, reference_id, note, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (product_id, batch_id, quantity_change, movement_type,
         reference_type, reference_id, note, created_by),
    )


def deduct(conn, product, quantity, *, movement_type="sale",
           reference_type=None, reference_id=None, created_by=None, note=None):
    """
    Removes `quantity` units from stock, returning the batches it came from
    as a list of (batch_id, quantity_taken, unit_cost_cents).

    For expiry-tracked products this consumes batches soonest-expiry-first
    (FEFO), splitting across batches when one is not enough.

    If the batches cannot cover the quantity, this raises rather than
    deducting what it can. The original version decremented the product
    total by the full amount regardless, which let `stock_quantity` and
    the sum of the batches drift apart silently - the till would keep
    selling stock that no batch could account for.
    """
    if quantity <= 0:
        raise StockError("Quantity must be greater than zero.")

    product_id = product["id"]

    if not product["track_expiry"]:
        if product["stock_quantity"] < quantity:
            raise StockError(
                f"Not enough stock for {product['name']} "
                f"({product['stock_quantity']} left)."
            )
        conn.execute(
            "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
            (quantity, product_id),
        )
        _log(conn, product_id=product_id, batch_id=None,
             quantity_change=-quantity, movement_type=movement_type,
             reference_type=reference_type, reference_id=reference_id,
             note=note, created_by=created_by)
        return [(None, quantity, product["cost_price_cents"])]

    batches = conn.execute(
        """
        SELECT id, quantity_remaining, cost_price_cents, expiry_date
        FROM batches
        WHERE product_id = ? AND quantity_remaining > 0
        ORDER BY expiry_date IS NULL, expiry_date ASC, id ASC
        """,
        (product_id,),
    ).fetchall()

    available = sum(b["quantity_remaining"] for b in batches)
    if available < quantity:
        raise StockError(
            f"Not enough stock for {product['name']} ({available} left "
            f"across batches)."
        )

    taken = []
    remaining = quantity
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch["quantity_remaining"], remaining)

        conn.execute(
            "UPDATE batches SET quantity_remaining = quantity_remaining - ? "
            "WHERE id = ?",
            (take, batch["id"]),
        )
        _log(conn, product_id=product_id, batch_id=batch["id"],
             quantity_change=-take, movement_type=movement_type,
             reference_type=reference_type, reference_id=reference_id,
             note=note, created_by=created_by)

        taken.append((batch["id"], take, batch["cost_price_cents"]))
        remaining -= take

    conn.execute(
        "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
        (quantity, product_id),
    )
    return taken


def restock(conn, *, product_id, batch_id, quantity, movement_type,
            reference_type=None, reference_id=None, created_by=None, note=None):
    """
    Puts units back - a return of saleable goods, or a void undoing a sale.
    Goes back to the exact batch it came from so expiry stays accurate.
    """
    if quantity <= 0:
        raise StockError("Quantity must be greater than zero.")

    if batch_id is not None:
        conn.execute(
            "UPDATE batches SET quantity_remaining = quantity_remaining + ? "
            "WHERE id = ?",
            (quantity, batch_id),
        )
    conn.execute(
        "UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?",
        (quantity, product_id),
    )
    _log(conn, product_id=product_id, batch_id=batch_id,
         quantity_change=quantity, movement_type=movement_type,
         reference_type=reference_type, reference_id=reference_id,
         note=note, created_by=created_by)


def record_stock_in(conn, *, product_id, quantity, cost_price_cents,
                    created_by, expiry_date=None, batch_number=None,
                    supplier_id=None):
    """
    Receives newly delivered stock. Expiry-tracked products get a new
    batch so FEFO has something to sort; everything else just increases
    the product total. Either way the ledger records it.

    Returns the new batch id, or None.
    """
    product = conn.execute(
        "SELECT * FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        raise StockError("Product not found.")
    if quantity <= 0:
        raise StockError("Quantity must be greater than zero.")

    batch_id = None
    if product["track_expiry"]:
        if not expiry_date:
            raise StockError(f"{product['name']} needs an expiry date.")
        cursor = conn.execute(
            """
            INSERT INTO batches
                (product_id, supplier_id, batch_number, cost_price_cents,
                 quantity_received, quantity_remaining, expiry_date, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, supplier_id, batch_number, cost_price_cents,
             quantity, quantity, expiry_date, created_by),
        )
        batch_id = cursor.lastrowid

    conn.execute(
        "UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?",
        (quantity, product_id),
    )
    # The latest delivery's cost is what the next sale's margin is measured
    # against, so keep the product's cost price current.
    if cost_price_cents and cost_price_cents != product["cost_price_cents"]:
        conn.execute(
            "UPDATE products SET cost_price_cents = ? WHERE id = ?",
            (cost_price_cents, product_id),
        )

    _log(conn, product_id=product_id, batch_id=batch_id,
         quantity_change=quantity, movement_type="stock_in",
         reference_type="manual_stock_in", created_by=created_by)

    return batch_id


def adjust(conn, *, product_id, new_quantity, reason, created_by, batch_id=None):
    """
    Corrects stock after a physical count, or writes off damaged and
    expired goods. The difference is logged, never the new figure alone -
    so "where did those twelve units go" always has an answer.
    """
    product = conn.execute(
        "SELECT * FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        raise StockError("Product not found.")
    if new_quantity < 0:
        raise StockError("Stock cannot be negative.")

    difference = new_quantity - product["stock_quantity"]
    if difference == 0:
        return 0

    movement_type = "count_adjustment"
    if reason in ("damaged", "expired"):
        movement_type = reason

    conn.execute(
        "UPDATE products SET stock_quantity = ? WHERE id = ?",
        (new_quantity, product_id),
    )
    if batch_id is not None:
        conn.execute(
            "UPDATE batches SET quantity_remaining = MAX(0, quantity_remaining + ?) "
            "WHERE id = ?",
            (difference, batch_id),
        )

    _log(conn, product_id=product_id, batch_id=batch_id,
         quantity_change=difference, movement_type=movement_type,
         reference_type="adjustment", note=reason, created_by=created_by)
    audit(conn, created_by, "stock_adjusted", "product", product_id,
          f"{product['stock_quantity']} -> {new_quantity} ({reason})")
    return difference


def write_off_expired(conn, *, batch_id, created_by, note=None):
    """
    Removes an expired batch's remaining units from saleable stock.
    Keeps the batch row - the loss is a fact the owner should be able
    to look back at, not something to delete.
    """
    batch = conn.execute(
        "SELECT * FROM batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if batch is None:
        raise StockError("Batch not found.")
    quantity = batch["quantity_remaining"]
    if quantity <= 0:
        return 0

    conn.execute(
        "UPDATE batches SET quantity_remaining = 0 WHERE id = ?", (batch_id,)
    )
    conn.execute(
        "UPDATE products SET stock_quantity = MAX(0, stock_quantity - ?) WHERE id = ?",
        (quantity, batch["product_id"]),
    )
    _log(conn, product_id=batch["product_id"], batch_id=batch_id,
         quantity_change=-quantity, movement_type="expired",
         reference_type="write_off", note=note, created_by=created_by)
    audit(conn, created_by, "expired_written_off", "batch", batch_id,
          f"{quantity} units")
    return quantity


# ------------------------------------------------------ INTEGRITY CHECKS --

def find_discrepancies():
    """
    Lists products whose cached stock level disagrees with the ledger, and
    expiry-tracked products whose total disagrees with the sum of their
    batches. On a healthy database this returns nothing; anything it finds
    is a bug or a crash, and the owner should be told rather than left to
    discover it as a shortfall at stock-take.
    """
    return query_all(
        """
        SELECT p.id, p.name, p.track_expiry, p.stock_quantity,
               COALESCE((SELECT SUM(quantity_change) FROM stock_movements m
                         WHERE m.product_id = p.id), 0) AS ledger_quantity,
               COALESCE((SELECT SUM(quantity_remaining) FROM batches b
                         WHERE b.product_id = p.id), 0) AS batch_quantity
        FROM products p
        WHERE p.stock_quantity <> ledger_quantity
           OR (p.track_expiry = 1 AND p.stock_quantity <> batch_quantity)
        ORDER BY p.name
        """
    )


def recalculate_stock(conn, product_id, created_by=None):
    """
    Rebuilds a product's cached stock level from the ledger. The ledger is
    append-only, so this is always safe to run and always converges.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(quantity_change), 0) AS total
        FROM stock_movements WHERE product_id = ?
        """,
        (product_id,),
    ).fetchone()
    conn.execute(
        "UPDATE products SET stock_quantity = ? WHERE id = ?",
        (row["total"], product_id),
    )
    audit(conn, created_by, "stock_recalculated", "product", product_id,
          f"set to {row['total']} from ledger")
    return row["total"]


def get_low_stock(limit=None):
    sql = """
        SELECT * FROM products
        WHERE active = 1 AND stock_quantity <= low_stock_threshold
        ORDER BY (stock_quantity = 0) DESC, stock_quantity ASC, name ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return query_all(sql)


def get_movements(product_id, limit=100):
    return query_all(
        """
        SELECT m.*, u.name AS user_name, b.expiry_date
        FROM stock_movements m
        LEFT JOIN users u ON u.id = m.created_by
        LEFT JOIN batches b ON b.id = m.batch_id
        WHERE m.product_id = ?
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT ?
        """,
        (product_id, limit),
    )
