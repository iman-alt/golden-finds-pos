"""
Deni - goods taken now, paid for later.

Deni is where a small shop's money most often goes missing, because it
lives in an exercise book and in people's memories. Here every item given
on deni is a row with who took it, their phone number, what, how many, at
what price and on which day - and every shilling paid back is a row too.
A balance is never a number someone typed; it is always the sum of those
rows.

Two rules keep it honest:

  * The goods leave the shelf when the deni is recorded, exactly like a
    sale. Otherwise stock would look fine while the shelf is empty.
  * The price is worked out here, with the same rules as the till
    (offer, then wholesale, then retail). Nobody types a price in.
"""

import re
from datetime import date

from ..db import audit, query_all, query_one
from ..money import format_money
from . import stock
from .offers import get_active_offer
from .sales import resolve_price


class DeniError(Exception):
    """Raised when a deni cannot be recorded. Message is user-safe."""


# ----------------------------------------------------------------- phones --

def normalise_phone(raw):
    """
    Accepts a Kenyan mobile number in the ways people actually write it -
    0712 345 678, +254 712 345 678, 254712345678, 712345678 - and stores
    one form, so the same person is always the same person.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("254") and len(digits) == 12:
        digits = "0" + digits[3:]
    elif len(digits) == 9 and digits[0] in "17":
        digits = "0" + digits

    if not re.fullmatch(r"0[17]\d{8}", digits):
        raise DeniError("Enter a Kenyan phone number, like 0712 345 678.")
    return digits


def format_phone(phone):
    """0712345678 -> 0712 345 678, which is how people read a number out."""
    if phone and len(phone) == 10:
        return f"{phone[:4]} {phone[4:7]} {phone[7:]}"
    return phone or ""


# ---------------------------------------------------------------- writing --

def record(conn, *, customer_name, phone, product_id, quantity, created_by,
           taken_on=None):
    """
    Records goods given on deni and takes them off the shelf.
    Must be called inside a transaction(). Returns the new deni id.
    """
    name = " ".join((customer_name or "").split())
    if not name:
        raise DeniError("Who is taking it? Enter their name.")
    phone = normalise_phone(phone)

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise DeniError("Enter how many they took.")
    if quantity <= 0:
        raise DeniError("Enter how many they took.")

    if taken_on:
        try:
            day = date.fromisoformat(str(taken_on))
        except ValueError:
            raise DeniError("That date isn't valid.")
        if day > date.today():
            raise DeniError("The day can't be in the future.")
    else:
        day = date.today()

    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        raise DeniError("Choose the item they took.")
    product = conn.execute(
        "SELECT * FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        raise DeniError("Choose the item they took.")
    if not product["active"]:
        raise DeniError(f"{product['name']} is no longer sold.")

    unit_price, _, _ = resolve_price(
        product, quantity, get_active_offer(product["id"])
    )
    total = unit_price * quantity

    cursor = conn.execute(
        """
        INSERT INTO deni (customer_name, phone, product_id, quantity,
                          unit_price_cents, total_cents, taken_on, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, phone, product["id"], quantity, unit_price, total,
         day.isoformat(), created_by),
    )
    deni_id = cursor.lastrowid

    # Raises if the stock isn't there, which rolls the whole deni back.
    stock.deduct(
        conn, product, quantity, movement_type="sale",
        reference_type="deni", reference_id=deni_id,
        created_by=created_by, note=f"deni: {name}",
    )

    audit(conn, created_by, "deni_recorded", "deni", deni_id,
          f"{name} {phone} {quantity} x {product['name']} = {total}")
    return deni_id


def record_payment(conn, *, phone, amount_cents, method, received_by):
    """
    Takes money against everything a phone number owes, clearing the
    oldest items first - the way a person paying off a tab thinks of it.
    Returns what is still owed afterwards.
    """
    phone = normalise_phone(phone)
    if method not in ("cash", "mpesa"):
        raise DeniError("Choose cash or M-Pesa.")
    if not amount_cents or amount_cents <= 0:
        raise DeniError("Enter how much they paid.")

    entries = conn.execute(
        """
        SELECT * FROM deni WHERE phone = ? AND status = 'open'
        ORDER BY taken_on, id
        """,
        (phone,),
    ).fetchall()
    if not entries:
        raise DeniError("That number doesn't owe anything.")

    owed = sum(e["total_cents"] - e["paid_cents"] for e in entries)
    if amount_cents > owed:
        raise DeniError(f"They only owe {format_money(owed)}.")

    remaining = amount_cents
    for entry in entries:
        if remaining <= 0:
            break
        applied = min(entry["total_cents"] - entry["paid_cents"], remaining)
        paid = entry["paid_cents"] + applied
        conn.execute(
            "UPDATE deni SET paid_cents = ?, status = ? WHERE id = ?",
            (paid, "paid" if paid == entry["total_cents"] else "open", entry["id"]),
        )
        remaining -= applied

    conn.execute(
        """
        INSERT INTO deni_payments (phone, customer_name, amount_cents, method, received_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (phone, entries[-1]["customer_name"], amount_cents, method, received_by),
    )
    audit(conn, received_by, "deni_payment", "deni", entries[0]["id"],
          f"{phone} paid {amount_cents} by {method}")
    return owed - amount_cents


def cancel(conn, deni_id, *, cancelled_by, reason):
    """
    Undoes a deni recorded by mistake and puts the goods back on the shelf,
    into the same batches they came from. The row is kept and marked, not
    deleted, so the book still shows that it happened and who undid it.
    """
    entry = conn.execute("SELECT * FROM deni WHERE id = ?", (deni_id,)).fetchone()
    if entry is None:
        raise DeniError("That deni wasn't found.")
    if entry["status"] == "cancelled":
        raise DeniError("That deni is already cancelled.")
    if entry["paid_cents"]:
        raise DeniError("Part of this has already been paid, so it can't be cancelled.")
    reason = (reason or "").strip()
    if not reason:
        raise DeniError("Say why it's being cancelled.")

    taken = conn.execute(
        """
        SELECT batch_id, quantity_change FROM stock_movements
        WHERE reference_type = 'deni' AND reference_id = ? AND movement_type = 'sale'
        """,
        (deni_id,),
    ).fetchall()
    for movement in taken:
        stock.restock(
            conn, product_id=entry["product_id"], batch_id=movement["batch_id"],
            quantity=-movement["quantity_change"], movement_type="void",
            reference_type="deni_cancel", reference_id=deni_id,
            created_by=cancelled_by, note=reason,
        )

    conn.execute(
        """
        UPDATE deni SET status = 'cancelled', cancelled_by = ?,
               cancelled_at = datetime('now'), cancel_reason = ?
        WHERE id = ?
        """,
        (cancelled_by, reason, deni_id),
    )
    audit(conn, cancelled_by, "deni_cancelled", "deni", deni_id, reason)


# ---------------------------------------------------------------- reading --

def summary():
    return query_one(
        """
        SELECT COALESCE(SUM(total_cents - paid_cents), 0) AS owed_cents,
               COUNT(DISTINCT phone)                     AS people,
               MIN(taken_on)                             AS oldest
        FROM deni WHERE status = 'open'
        """
    )


def debtors():
    """Everyone who owes, oldest debt first - those are the ones to chase."""
    return query_all(
        """
        SELECT d.phone,
               (SELECT customer_name FROM deni latest
                WHERE latest.phone = d.phone ORDER BY latest.id DESC LIMIT 1)
                   AS customer_name,
               SUM(d.total_cents - d.paid_cents) AS owed_cents,
               COUNT(*)                          AS item_count,
               MIN(d.taken_on)                   AS oldest
        FROM deni d
        WHERE d.status = 'open'
        GROUP BY d.phone
        ORDER BY oldest, owed_cents DESC
        """
    )


def entries(*, search=None, status=None, phone=None, limit=200):
    where, params = ["1 = 1"], []
    if status:
        where.append("d.status = ?")
        params.append(status)
    if phone:
        where.append("d.phone = ?")
        params.append(phone)
    if search:
        digits = re.sub(r"\D", "", search)
        clause = "d.customer_name LIKE ? OR p.name LIKE ?"
        params.extend([f"%{search}%", f"%{search}%"])
        if digits:
            clause += " OR d.phone LIKE ?"
            params.append(f"%{digits[-9:]}%")
        where.append(f"({clause})")
    params.append(limit)

    return query_all(
        f"""
        SELECT d.*, p.name AS product_name, p.category, p.image_path,
               u.name AS recorded_by, c.name AS cancelled_by_name
        FROM deni d
        JOIN products p ON p.id = d.product_id
        LEFT JOIN users u ON u.id = d.created_by
        LEFT JOIN users c ON c.id = d.cancelled_by
        WHERE {' AND '.join(where)}
        ORDER BY d.taken_on DESC, d.id DESC
        LIMIT ?
        """,
        params,
    )


def payments(phone):
    return query_all(
        """
        SELECT dp.*, u.name AS received_by_name
        FROM deni_payments dp
        LEFT JOIN users u ON u.id = dp.received_by
        WHERE dp.phone = ?
        ORDER BY dp.created_at DESC, dp.id DESC
        """,
        (phone,),
    )
