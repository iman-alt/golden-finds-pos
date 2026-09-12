"""
Sales, voids and returns.

The single most important rule here: the price charged is decided by the
server, from the database, at the moment of sale. The browser sends only
product ids and quantities. It cannot send a price.

The original version trusted `get_price_for_quantity` alone and never
consulted the offers table, so the cart showed the customer an offer
price while the database recorded full retail. Pricing now happens in one
place - `resolve_price` - which both the cart preview and the recorded
sale go through, so what the customer is quoted is what the shop books.
"""

from datetime import datetime

from ..db import audit, query_all, query_one
from . import stock
from .offers import get_active_offer


class SaleError(Exception):
    """Raised when a sale cannot be completed. Message is user-safe."""


def resolve_price(product, quantity, offer=None):
    """
    Decides what one unit costs, and why.

    Order of precedence:
      1. An active, admin-approved offer.
      2. The wholesale price, once quantity reaches the product's threshold.
      3. Retail.

    Returns (unit_price_cents, price_basis, offer_id).
    """
    if offer is not None:
        return offer["offer_price_cents"], "offer", offer["id"]
    if quantity >= product["wholesale_min_qty"]:
        return product["wholesale_price_cents"], "wholesale", None
    return product["retail_price_cents"], "retail", None


def price_cart(items, conn=None):
    """
    Prices a whole cart without recording anything - used by the sell
    screen to show a total, and by `record_sale` to compute the real one.
    Because both go through this, the preview and the sale cannot diverge.

    `items` is [{"product_id": int, "quantity": int}, ...].
    Returns (lines, subtotal_cents).
    """
    execute = conn.execute if conn is not None else None

    lines = []
    subtotal = 0
    seen = set()

    for item in items:
        try:
            product_id = int(item["product_id"])
            quantity = int(item["quantity"])
        except (KeyError, TypeError, ValueError):
            raise SaleError("Cart contains an invalid item.")

        if quantity <= 0:
            raise SaleError("Quantity must be greater than zero.")
        if product_id in seen:
            raise SaleError("The same product appears twice in the cart.")
        seen.add(product_id)

        if execute:
            product = execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()
        else:
            product = query_one("SELECT * FROM products WHERE id = ?", (product_id,))

        if product is None:
            raise SaleError("A product in the cart no longer exists.")
        if not product["active"]:
            raise SaleError(f"{product['name']} is no longer sold.")

        offer = get_active_offer(product["id"])
        unit_price, basis, offer_id = resolve_price(product, quantity, offer)
        line_total = unit_price * quantity
        subtotal += line_total

        lines.append({
            "product": product,
            "product_id": product["id"],
            "name": product["name"],
            "quantity": quantity,
            "unit_price_cents": unit_price,
            "line_total_cents": line_total,
            "price_basis": basis,
            "offer_id": offer_id,
        })

    return lines, subtotal


def _next_receipt_number(conn):
    """
    Human-readable receipt numbers, restarting each day: GF-20260912-0007.
    A cashier can read one over the phone; a bare autoincrement id cannot
    be checked against a paper receipt at a glance.
    """
    today = datetime.now().strftime("%Y%m%d")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM sales WHERE receipt_number LIKE ?",
        (f"GF-{today}-%",),
    ).fetchone()
    return f"GF-{today}-{row['n'] + 1:04d}"


def record_sale(conn, *, cashier_id, items, payment_method,
                amount_paid_cents=None, customer_id=None):
    """
    Records a complete sale and deducts the stock it consumed.

    Must be called inside a `transaction()`. Stock is validated for every
    line before anything is written, and the caller's transaction rolls
    the whole thing back if any single line fails - there is no such thing
    as a half-recorded sale.

    Returns the new sale id.
    """
    if not items:
        raise SaleError("Cart is empty.")
    if payment_method not in ("cash", "mpesa", "credit"):
        raise SaleError("Choose a valid payment method.")

    lines, subtotal = price_cart(items, conn=conn)
    total = subtotal

    # Payment checks, before any stock moves.
    if payment_method == "credit":
        if not customer_id:
            raise SaleError("A credit sale needs a customer account.")
        customer = conn.execute(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if customer is None:
            raise SaleError("Customer not found.")
        new_balance = customer["credit_balance_cents"] + total
        if customer["credit_limit_cents"] and new_balance > customer["credit_limit_cents"]:
            raise SaleError(
                f"{customer['name']} would go over their credit limit."
            )
        paid = 0
        change = 0
    else:
        paid = total if amount_paid_cents is None else amount_paid_cents
        if paid < total:
            raise SaleError("Amount paid is less than the total.")
        change = paid - total

    cursor = conn.execute(
        """
        INSERT INTO sales (receipt_number, customer_id, cashier_id,
                           subtotal_cents, discount_cents, total_cents,
                           amount_paid_cents, change_cents, payment_method)
        VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (_next_receipt_number(conn), customer_id, cashier_id,
         subtotal, total, paid, change, payment_method),
    )
    sale_id = cursor.lastrowid

    # Deduct stock. FEFO may split one cart line across several batches,
    # so one cart line can become several sale_items rows - that is what
    # makes a later return go back to the right batch.
    for line in lines:
        consumed = stock.deduct(
            conn, line["product"], line["quantity"],
            movement_type="sale", reference_type="sale",
            reference_id=sale_id, created_by=cashier_id,
        )
        for batch_id, quantity_taken, unit_cost in consumed:
            conn.execute(
                """
                INSERT INTO sale_items
                    (sale_id, product_id, batch_id, quantity, unit_price_cents,
                     line_total_cents, price_basis, offer_id, unit_cost_cents)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sale_id, line["product_id"], batch_id, quantity_taken,
                 line["unit_price_cents"], line["unit_price_cents"] * quantity_taken,
                 line["price_basis"], line["offer_id"], unit_cost),
            )

    conn.execute(
        """
        INSERT INTO payments (sale_id, customer_id, amount_cents, method, created_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sale_id, customer_id, total, payment_method, cashier_id),
    )

    if payment_method == "credit":
        conn.execute(
            "UPDATE customers SET credit_balance_cents = credit_balance_cents + ? "
            "WHERE id = ?",
            (total, customer_id),
        )

    return sale_id


def get_sale(sale_id):
    return query_one(
        """
        SELECT s.*, u.name AS cashier_name, c.name AS customer_name,
               v.name AS voided_by_name
        FROM sales s
        LEFT JOIN users u ON u.id = s.cashier_id
        LEFT JOIN users v ON v.id = s.voided_by
        LEFT JOIN customers c ON c.id = s.customer_id
        WHERE s.id = ?
        """,
        (sale_id,),
    )


def get_sale_by_receipt(receipt_number):
    row = query_one(
        "SELECT id FROM sales WHERE receipt_number = ?", (receipt_number,)
    )
    return get_sale(row["id"]) if row else None


def get_sale_items(sale_id):
    """
    Sale lines, merged back to one row per product. FEFO may have split a
    line across batches, but the customer bought one thing and the receipt
    should say so.
    """
    return query_all(
        """
        SELECT product_id,
               MIN(p.name)               AS name,
               MIN(p.unit_type)          AS unit_type,
               SUM(si.quantity)          AS quantity,
               SUM(si.quantity_returned) AS quantity_returned,
               MIN(si.unit_price_cents)  AS unit_price_cents,
               SUM(si.line_total_cents)  AS line_total_cents,
               MIN(si.price_basis)       AS price_basis
        FROM sale_items si
        JOIN products p ON p.id = si.product_id
        WHERE si.sale_id = ?
        GROUP BY si.product_id, si.unit_price_cents, si.price_basis
        ORDER BY MIN(si.id)
        """,
        (sale_id,),
    )


def list_sales(*, date_from=None, date_to=None, cashier_id=None,
               search=None, limit=100, offset=0):
    """Sales history with the filters the owner actually asks for."""
    where = ["1 = 1"]
    params = []

    if date_from:
        where.append("date(s.created_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(s.created_at) <= date(?)")
        params.append(date_to)
    if cashier_id:
        where.append("s.cashier_id = ?")
        params.append(cashier_id)
    if search:
        where.append("(s.receipt_number LIKE ? OR c.name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    params.extend([limit, offset])
    return query_all(
        f"""
        SELECT s.*, u.name AS cashier_name, c.name AS customer_name,
               (SELECT SUM(quantity) FROM sale_items WHERE sale_id = s.id)
                   AS item_count
        FROM sales s
        LEFT JOIN users u ON u.id = s.cashier_id
        LEFT JOIN customers c ON c.id = s.customer_id
        WHERE {' AND '.join(where)}
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT ? OFFSET ?
        """,
        params,
    )


# ------------------------------------------------------------------ VOID --

def void_sale(conn, sale_id, *, voided_by, reason):
    """
    Cancels a whole sale and puts the stock back where it came from.

    The sale row is kept and marked voided rather than deleted - a till
    that can make transactions disappear is a till that can be stolen
    from. Reports exclude voided sales; the audit trail keeps them.
    """
    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if sale is None:
        raise SaleError("Sale not found.")
    if sale["status"] == "voided":
        raise SaleError("That sale is already voided.")
    if not (reason or "").strip():
        raise SaleError("Give a reason for the void.")

    returned = conn.execute(
        "SELECT COALESCE(SUM(quantity_returned), 0) AS n FROM sale_items "
        "WHERE sale_id = ?",
        (sale_id,),
    ).fetchone()
    if returned["n"]:
        raise SaleError(
            "This sale already has returns against it. Return the rest "
            "instead of voiding."
        )

    items = conn.execute(
        "SELECT * FROM sale_items WHERE sale_id = ?", (sale_id,)
    ).fetchall()
    for item in items:
        stock.restock(
            conn, product_id=item["product_id"], batch_id=item["batch_id"],
            quantity=item["quantity"], movement_type="void",
            reference_type="sale_void", reference_id=sale_id,
            created_by=voided_by, note=reason,
        )

    if sale["payment_method"] == "credit" and sale["customer_id"]:
        conn.execute(
            "UPDATE customers SET credit_balance_cents = credit_balance_cents - ? "
            "WHERE id = ?",
            (sale["total_cents"], sale["customer_id"]),
        )

    conn.execute(
        """
        UPDATE sales SET status = 'voided', voided_by = ?,
                         voided_at = datetime('now'), void_reason = ?
        WHERE id = ?
        """,
        (voided_by, reason.strip(), sale_id),
    )
    conn.execute(
        """
        INSERT INTO payments (sale_id, customer_id, amount_cents, method,
                              note, created_by)
        VALUES (?, ?, ?, ?, 'void', ?)
        """,
        (sale_id, sale["customer_id"], -sale["total_cents"],
         sale["payment_method"], voided_by),
    )
    audit(conn, voided_by, "sale_voided", "sale", sale_id,
          f"{sale['receipt_number']} total={sale['total_cents']} reason={reason}")


# ---------------------------------------------------------------- RETURN --

def record_return(conn, *, sale_id, product_id, quantity, reason,
                  restocked, created_by):
    """
    Takes back part of a sale.

    Refunds at the price actually paid, not the current price - if the
    product has gone up since, the customer is still owed what they gave.
    Goods that come back saleable go to the batch they were sold from;
    goods that do not are written off as damaged, so the shelf count stays
    honest either way.

    Returns the refunded amount in cents.
    """
    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if sale is None:
        raise SaleError("Sale not found.")
    if sale["status"] == "voided":
        raise SaleError("That sale was voided; there is nothing to return.")
    if quantity <= 0:
        raise SaleError("Return quantity must be greater than zero.")

    # Walk the sale's lines for this product oldest batch first, so the
    # returned units are attributed the same way they were sold.
    lines = conn.execute(
        """
        SELECT * FROM sale_items
        WHERE sale_id = ? AND product_id = ? AND quantity > quantity_returned
        ORDER BY id
        """,
        (sale_id, product_id),
    ).fetchall()

    outstanding = sum(line["quantity"] - line["quantity_returned"] for line in lines)
    if outstanding == 0:
        raise SaleError("That item has already been fully returned.")
    if quantity > outstanding:
        raise SaleError(f"Only {outstanding} of that item can still be returned.")

    remaining = quantity
    refunded = 0

    for line in lines:
        if remaining <= 0:
            break
        take = min(line["quantity"] - line["quantity_returned"], remaining)
        line_refund = line["unit_price_cents"] * take
        refunded += line_refund

        conn.execute(
            "UPDATE sale_items SET quantity_returned = quantity_returned + ? "
            "WHERE id = ?",
            (take, line["id"]),
        )
        conn.execute(
            """
            INSERT INTO returns (sale_id, sale_item_id, quantity, reason,
                                 restocked, refunded_cents, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sale_id, line["id"], take, reason, int(bool(restocked)),
             line_refund, created_by),
        )

        if restocked:
            stock.restock(
                conn, product_id=product_id, batch_id=line["batch_id"],
                quantity=take, movement_type="return",
                reference_type="return", reference_id=sale_id,
                created_by=created_by, note=reason,
            )
        # Not restocked: the goods are gone. Nothing returns to stock, and
        # the sale's own deduction already accounts for them leaving.

        remaining -= take

    if sale["payment_method"] == "credit" and sale["customer_id"]:
        conn.execute(
            "UPDATE customers SET credit_balance_cents = credit_balance_cents - ? "
            "WHERE id = ?",
            (refunded, sale["customer_id"]),
        )

    conn.execute(
        """
        INSERT INTO payments (sale_id, customer_id, amount_cents, method,
                              note, created_by)
        VALUES (?, ?, ?, ?, 'refund', ?)
        """,
        (sale_id, sale["customer_id"], -refunded, sale["payment_method"],
         created_by),
    )
    audit(conn, created_by, "sale_returned", "sale", sale_id,
          f"product={product_id} qty={quantity} refund={refunded} "
          f"restocked={bool(restocked)}")

    return refunded
