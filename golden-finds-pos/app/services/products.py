"""
Product catalogue.
"""

from ..db import audit, query_all, query_one
from .icons import icon_for

CATEGORIES = (
    "Groceries & Food Items",
    "Personal Care & Cosmetics",
    "Household Essentials",
    "Baby Care Products",
    "Fashion & Accessories",
    "Other",
)

UNIT_TYPES = ("piece", "kg", "litre", "carton", "bag", "packet")


class ProductError(Exception):
    """Raised when a product cannot be saved. Message is user-safe."""


def get_by_barcode(barcode):
    return query_one("SELECT * FROM products WHERE barcode = ?", ((barcode or "").strip(),))


def get(product_id):
    return query_one("SELECT * FROM products WHERE id = ?", (product_id,))


def search(query, limit=15):
    """
    Typed search for the cashier's box.

    Matches on name and on barcode, because a cashier holding a damaged
    label often reads off the last few digits instead of scanning. Exact
    barcode matches sort first, then names that start with the query,
    then anything else containing it - so the obvious answer is at the top.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    pattern = f"%{query}%"
    return query_all(
        """
        SELECT * FROM products
        WHERE active = 1 AND (name LIKE ? OR barcode LIKE ?)
        ORDER BY
            CASE WHEN barcode = ?        THEN 0
                 WHEN name LIKE ?        THEN 1
                 ELSE 2 END,
            name
        LIMIT ?
        """,
        (pattern, pattern, query, f"{query}%", limit),
    )


def list_products(*, search_term=None, category=None, include_inactive=False,
                  limit=200, offset=0):
    where = []
    params = []
    if not include_inactive:
        where.append("active = 1")
    if search_term:
        where.append("(name LIKE ? OR barcode LIKE ?)")
        params.extend([f"%{search_term}%", f"%{search_term}%"])
    if category:
        where.append("category = ?")
        params.append(category)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.extend([limit, offset])
    return query_all(
        f"SELECT * FROM products {clause} ORDER BY name LIMIT ? OFFSET ?", params
    )


def _validate(*, name, barcode, retail_price_cents, wholesale_price_cents,
              cost_price_cents, wholesale_min_qty, low_stock_threshold):
    if not (barcode or "").strip():
        raise ProductError("Barcode is required.")
    if not (name or "").strip():
        raise ProductError("Product name is required.")
    if retail_price_cents <= 0:
        raise ProductError("Retail price must be greater than zero.")
    if wholesale_price_cents <= 0:
        raise ProductError("Wholesale price must be greater than zero.")
    if wholesale_price_cents > retail_price_cents:
        raise ProductError("Wholesale price cannot be above the retail price.")
    if wholesale_min_qty < 1:
        raise ProductError("Wholesale minimum quantity must be at least 1.")
    if low_stock_threshold < 0:
        raise ProductError("Low stock threshold cannot be negative.")
    # Selling below cost is a decision, not a typo - but it should be a
    # decision someone made on purpose, so it is surfaced, not blocked.
    return cost_price_cents > retail_price_cents


def create(conn, *, barcode, name, category, unit_type, retail_price_cents,
           wholesale_price_cents, wholesale_min_qty, cost_price_cents,
           low_stock_threshold, track_expiry, created_by, image_path=None):
    """
    Registers a new product. Returns (product_id, warning_or_None).
    """
    barcode = (barcode or "").strip()
    name = (name or "").strip()

    below_cost = _validate(
        name=name, barcode=barcode,
        retail_price_cents=retail_price_cents,
        wholesale_price_cents=wholesale_price_cents,
        cost_price_cents=cost_price_cents,
        wholesale_min_qty=wholesale_min_qty,
        low_stock_threshold=low_stock_threshold,
    )

    existing = conn.execute(
        "SELECT id, name, active FROM products WHERE barcode = ?", (barcode,)
    ).fetchone()
    if existing is not None:
        if not existing["active"]:
            raise ProductError(
                f"That barcode belongs to {existing['name']}, which was "
                f"discontinued. Reactivate it instead of adding it again."
            )
        raise ProductError(f"That barcode is already registered to {existing['name']}.")

    if unit_type not in UNIT_TYPES:
        unit_type = "piece"

    cursor = conn.execute(
        """
        INSERT INTO products
            (barcode, name, category, unit_type, retail_price_cents,
             wholesale_price_cents, wholesale_min_qty, cost_price_cents,
             low_stock_threshold, track_expiry, image_path, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (barcode, name, category, unit_type, retail_price_cents,
         wholesale_price_cents, wholesale_min_qty, cost_price_cents,
         low_stock_threshold, int(bool(track_expiry)), image_path, created_by),
    )
    product_id = cursor.lastrowid
    audit(conn, created_by, "product_created", "product", product_id,
          f"{name} ({barcode})")

    warning = (
        "Heads up: the cost price is above the retail price, so every sale "
        "loses money."
    ) if below_cost else None
    return product_id, warning


def update(conn, product_id, *, updated_by, **fields):
    """
    Edits a product. Price changes are written to the audit log with their
    before and after values, because "who dropped the price on the sugar"
    is a question a shop owner will eventually need answered.
    """
    product = get(product_id)
    if product is None:
        raise ProductError("Product not found.")

    allowed = {
        "name", "category", "unit_type", "retail_price_cents",
        "wholesale_price_cents", "wholesale_min_qty", "cost_price_cents",
        "low_stock_threshold", "track_expiry", "image_path", "active",
    }
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not changes:
        return

    merged = {key: changes.get(key, product[key]) for key in (
        "retail_price_cents", "wholesale_price_cents", "cost_price_cents",
        "wholesale_min_qty", "low_stock_threshold",
    )}
    below_cost = _validate(
        name=changes.get("name", product["name"]),
        barcode=product["barcode"], **merged,
    )

    assignments = ", ".join(f"{key} = ?" for key in changes)
    conn.execute(
        f"UPDATE products SET {assignments} WHERE id = ?",
        [*changes.values(), product_id],
    )

    price_changes = [
        f"{key}: {product[key]} -> {value}"
        for key, value in changes.items()
        if key.endswith("_cents") and product[key] != value
    ]
    if price_changes:
        audit(conn, updated_by, "price_changed", "product", product_id,
              "; ".join(price_changes))
    else:
        audit(conn, updated_by, "product_updated", "product", product_id,
              ", ".join(changes))

    return (
        "Heads up: the cost price is above the retail price, so every sale "
        "loses money."
    ) if below_cost else None


def to_json(product, offer=None):
    """The shape the sell and stock-in screens expect."""
    return {
        "found": True,
        "id": product["id"],
        "name": product["name"],
        "icon": icon_for(product["name"], product["category"]),
        "barcode": product["barcode"],
        "unit_type": product["unit_type"],
        "retail_price_cents": product["retail_price_cents"],
        "wholesale_price_cents": product["wholesale_price_cents"],
        "wholesale_min_qty": product["wholesale_min_qty"],
        "stock_quantity": product["stock_quantity"],
        "low_stock_threshold": product["low_stock_threshold"],
        "track_expiry": bool(product["track_expiry"]),
        "offer_price_cents": offer["offer_price_cents"] if offer else None,
    }
