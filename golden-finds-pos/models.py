from datetime import date
from database import get_connection


def get_product_by_barcode(barcode):
    """
    Looks up a single product by its exact barcode.
    Returns the product row, or None if no match (e.g. new/unregistered item).
    Uses the indexed barcode column, so this stays fast even with thousands
    of products.
    """
    conn = get_connection()
    product = conn.execute(
        "SELECT * FROM products WHERE barcode = ?", (barcode,)
    ).fetchone()
    conn.close()
    return product


def search_products(query, limit=15):
    """
    Typed search for the cashier's search box (e.g. "coke", "500ml").
    Matches anywhere in the name using LIKE with wildcards on both sides.
    limit keeps results snappy - the cashier doesn't need 200 matches,
    just enough to spot the right one.
    """
    conn = get_connection()
    like_pattern = f"%{query}%"
    results = conn.execute(
        "SELECT * FROM products WHERE name LIKE ? ORDER BY name LIMIT ?",
        (like_pattern, limit)
    ).fetchall()
    conn.close()
    return results


def add_product(barcode, name, category, unit_type, retail_price,
                 wholesale_price, wholesale_min_qty, cost_price,
                 low_stock_threshold=5, track_expiry=False, image_path=None):
    """
    Registers a brand-new product. Called the first time a barcode is
    scanned and nothing matches in the database.
    Returns (success: bool, message: str) so the calling route can show
    a clear response either way.
    """
    if get_product_by_barcode(barcode) is not None:
        return False, "A product with this barcode already exists."

    conn = get_connection()
    conn.execute("""
        INSERT INTO products (
            barcode, name, category, unit_type,
            retail_price, wholesale_price, wholesale_min_qty,
            cost_price, low_stock_threshold, track_expiry, image_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        barcode, name, category, unit_type,
        retail_price, wholesale_price, wholesale_min_qty,
        cost_price, low_stock_threshold, int(track_expiry), image_path
    ))
    conn.commit()
    conn.close()
    return True, "Product added successfully."


def get_low_stock_products():
    """
    Returns every product where current stock is at or below its
    low_stock_threshold - powers the dashboard alert list.
    """
    conn = get_connection()
    results = conn.execute("""
        SELECT * FROM products
        WHERE stock_quantity <= low_stock_threshold
        ORDER BY stock_quantity ASC
    """).fetchall()
    conn.close()
    return results


def get_price_for_quantity(product, quantity):
    """
    Decides retail vs wholesale price based on how many units are being
    bought. Returns (unit_price, is_wholesale).
    """
    if quantity >= product["wholesale_min_qty"]:
        return product["wholesale_price"], True
    return product["retail_price"], False


def _deduct_stock_fefo(conn, product, quantity, reference_type,
                        reference_id, created_by):
    """
    Internal helper: deducts `quantity` units of a product from stock.
    If the product tracks expiry, pulls from batches soonest-expiry-first,
    splitting across batches if needed. Otherwise deducts the plain
    stock_quantity total. Every deduction is logged to stock_movements.

    Returns a list of (batch_id, quantity_taken) pairs - batch_id is None
    for non-expiry-tracked products. sale_items needs this to record
    which batch each line came from.
    """
    taken = []

    if not product["track_expiry"]:
        conn.execute(
            "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
            (quantity, product["id"])
        )
        conn.execute("""
            INSERT INTO stock_movements
                (product_id, batch_id, quantity_change, movement_type,
                 reference_type, reference_id, created_by)
            VALUES (?, NULL, ?, 'sale', ?, ?, ?)
        """, (product["id"], -quantity, reference_type, reference_id, created_by))
        taken.append((None, quantity))
        return taken

    # Expiry-tracked: pull from batches, soonest expiry first
    remaining_needed = quantity
    batches = conn.execute("""
        SELECT * FROM batches
        WHERE product_id = ? AND quantity_remaining > 0
        ORDER BY expiry_date ASC
    """, (product["id"],)).fetchall()

    for batch in batches:
        if remaining_needed <= 0:
            break
        take = min(batch["quantity_remaining"], remaining_needed)

        conn.execute(
            "UPDATE batches SET quantity_remaining = quantity_remaining - ? WHERE id = ?",
            (take, batch["id"])
        )
        conn.execute("""
            INSERT INTO stock_movements
                (product_id, batch_id, quantity_change, movement_type,
                 reference_type, reference_id, created_by)
            VALUES (?, ?, ?, 'sale', ?, ?, ?)
        """, (product["id"], batch["id"], -take, reference_type, reference_id, created_by))

        taken.append((batch["id"], take))
        remaining_needed -= take

    conn.execute(
        "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
        (quantity, product["id"])
    )
    return taken


def record_sale(cashier_id, cart_items, payment_method,
                 amount_paid=None, customer_id=None):
    """
    Records a full sale transaction.

    cart_items: list of dicts like {'product_id': 3, 'quantity': 2}
    Price is resolved automatically per item via get_price_for_quantity.

    Returns (success: bool, message: str, sale_id or None).
    Everything happens in one transaction - if stock is insufficient for
    any item, nothing is saved at all.
    """
    conn = get_connection()

    try:
        # First pass: validate stock is sufficient for every item
        # before changing anything (avoids partial sales on failure)
        resolved_items = []
        total = 0

        for item in cart_items:
            product = conn.execute(
                "SELECT * FROM products WHERE id = ?", (item["product_id"],)
            ).fetchone()

            if product is None:
                conn.close()
                return False, f"Product id {item['product_id']} not found.", None

            if product["stock_quantity"] < item["quantity"]:
                conn.close()
                return False, f"Not enough stock for {product['name']}.", None

            unit_price, is_wholesale = get_price_for_quantity(product, item["quantity"])
            line_total = unit_price * item["quantity"]
            total += line_total

            resolved_items.append({
                "product": product,
                "quantity": item["quantity"],
                "unit_price": unit_price,
                "is_wholesale": is_wholesale
            })

        # Second pass: create the sale record
        cursor = conn.execute("""
            INSERT INTO sales (customer_id, cashier_id, total, payment_method, amount_paid)
            VALUES (?, ?, ?, ?, ?)
        """, (customer_id, cashier_id, total, payment_method, amount_paid))
        sale_id = cursor.lastrowid

        # Third pass: deduct stock (FEFO-aware) and write sale_items,
        # one row per batch actually consumed
        for resolved in resolved_items:
            product = resolved["product"]
            batches_used = _deduct_stock_fefo(
                conn, product, resolved["quantity"],
                reference_type="sale", reference_id=sale_id, created_by=cashier_id
            )
            for batch_id, qty_from_batch in batches_used:
                conn.execute("""
                    INSERT INTO sale_items
                        (sale_id, product_id, batch_id, quantity, unit_price, was_wholesale_price)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (sale_id, product["id"], batch_id, qty_from_batch,
                      resolved["unit_price"], int(resolved["is_wholesale"])))

        conn.commit()
        conn.close()
        return True, "Sale recorded.", sale_id

    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Sale failed: {e}", None


def _tier_for_days_left(days_left):
    """
    Maps days-until-expiry to a tier, per the design doc thresholds.
    Returns None if it's not close enough to expiry to flag yet.
    """
    if days_left < 0:
        return "expired"
    elif days_left <= 3:
        return "urgent"
    elif days_left <= 7:
        return "consider_offer"
    elif days_left <= 14:
        return "warning"
    elif days_left <= 30:
        return "monitor"
    return None


def get_expiry_alerts():
    """
    Returns every batch with remaining stock that's within 30 days of
    expiry (or already expired), tagged with its tier and days_left.
    This is what feeds the admin's expiry/offer management screen.
    Ordered soonest-expiring first, since that's what needs attention first.
    """
    conn = get_connection()
    batches = conn.execute("""
        SELECT batches.*, products.name AS product_name, products.retail_price
        FROM batches
        JOIN products ON products.id = batches.product_id
        WHERE batches.quantity_remaining > 0
          AND batches.expiry_date IS NOT NULL
        ORDER BY batches.expiry_date ASC
    """).fetchall()
    conn.close()

    alerts = []
    today = date.today()
    for batch in batches:
        expiry = date.fromisoformat(batch["expiry_date"])
        days_left = (expiry - today).days
        tier = _tier_for_days_left(days_left)
        if tier is not None:
            alerts.append({
                "batch_id": batch["id"],
                "product_id": batch["product_id"],
                "product_name": batch["product_name"],
                "retail_price": batch["retail_price"],
                "quantity_remaining": batch["quantity_remaining"],
                "expiry_date": batch["expiry_date"],
                "days_left": days_left,
                "tier": tier
            })
    return alerts


def create_offer(product_id, offer_price, tier, approved_by, batch_id=None, end_date=None):
    """
    Records an admin-approved offer price for a product (optionally tied
    to one specific batch). This is only ever called from the admin's
    "put on offer" flow after they pick an explicit price - never
    auto-triggered by the system.
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO offers (product_id, batch_id, offer_price, tier, approved_by, end_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (product_id, batch_id, offer_price, tier, approved_by, end_date))
    conn.commit()
    conn.close()


def record_stock_in(product_id, quantity, cost_price, created_by=None,
                     expiry_date=None, batch_number=None, supplier_id=None):
    """
    Adds newly received stock. If the product tracks expiry, creates a
    new batch (so FEFO has something to sort later). Otherwise just
    bumps stock_quantity directly. Either way, logs a stock_in row.
    Returns (success: bool, message: str).
    """
    conn = get_connection()
    try:
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            conn.close()
            return False, "Product not found."

        if quantity <= 0:
            conn.close()
            return False, "Quantity must be greater than zero."

        batch_id = None
        if product["track_expiry"]:
            if not expiry_date:
                conn.close()
                return False, "This product requires an expiry date."
            cursor = conn.execute("""
                INSERT INTO batches
                    (product_id, supplier_id, batch_number, cost_price,
                     quantity_received, quantity_remaining, expiry_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (product_id, supplier_id, batch_number, cost_price,
                  quantity, quantity, expiry_date))
            batch_id = cursor.lastrowid

        conn.execute(
            "UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?",
            (quantity, product_id)
        )
        conn.execute("""
            INSERT INTO stock_movements
                (product_id, batch_id, quantity_change, movement_type,
                 reference_type, reference_id, created_by)
            VALUES (?, ?, ?, 'stock_in', 'manual_stock_in', NULL, ?)
        """, (product_id, batch_id, quantity, created_by))

        conn.commit()
        conn.close()
        return True, "Stock added."

    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Stock-in failed: {e}"


def get_active_offer(product_id):
    """
    Returns the currently active offer for a product, if any, else None.
    Used at checkout so the cashier can apply an existing approved offer
    without being able to create one themselves.
    """
    conn = get_connection()
    offer = conn.execute("""
        SELECT * FROM offers
        WHERE product_id = ? AND active = 1
        ORDER BY start_date DESC
        LIMIT 1
    """, (product_id,)).fetchone()
    conn.close()
    return offer
