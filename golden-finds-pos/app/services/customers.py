"""
Customer accounts and the shop credit book.

Credit is the part of a small shop's money that most often goes missing,
because it usually lives in an exercise book. Here every change to a
balance is a `payments` row, so a balance is always explainable by the
entries that produced it rather than being a number someone edited.
"""

from ..db import audit, query_all, query_one


class CustomerError(Exception):
    """Raised when a customer operation cannot proceed. Message is user-safe."""


def get(customer_id):
    return query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))


def search(term, limit=15):
    term = (term or "").strip()
    if not term:
        return []
    pattern = f"%{term}%"
    return query_all(
        """
        SELECT * FROM customers
        WHERE name LIKE ? OR phone LIKE ?
        ORDER BY name LIMIT ?
        """,
        (pattern, pattern, limit),
    )


def list_customers():
    return query_all(
        "SELECT * FROM customers ORDER BY credit_balance_cents DESC, name"
    )


def create(conn, *, name, phone=None, is_wholesale=False,
           credit_limit_cents=0, created_by=None):
    name = (name or "").strip()
    if not name:
        raise CustomerError("Customer name is required.")

    cursor = conn.execute(
        """
        INSERT INTO customers (name, phone, is_wholesale, credit_limit_cents)
        VALUES (?, ?, ?, ?)
        """,
        (name, (phone or "").strip() or None, int(bool(is_wholesale)),
         credit_limit_cents),
    )
    customer_id = cursor.lastrowid
    audit(conn, created_by, "customer_created", "customer", customer_id, name)
    return customer_id


def record_payment(conn, *, customer_id, amount_cents, method, created_by,
                   note=None):
    """
    Takes a payment against a customer's outstanding credit.

    Refuses overpayment rather than letting a balance go negative - a
    negative balance in a credit book is almost always a keying error, and
    it is far easier to fix at the counter than at the month end.
    """
    customer = get(customer_id)
    if customer is None:
        raise CustomerError("Customer not found.")
    if amount_cents <= 0:
        raise CustomerError("Payment must be greater than zero.")
    if amount_cents > customer["credit_balance_cents"]:
        raise CustomerError(
            f"{customer['name']} only owes "
            f"{customer['credit_balance_cents'] / 100:,.2f}."
        )

    conn.execute(
        "UPDATE customers SET credit_balance_cents = credit_balance_cents - ? "
        "WHERE id = ?",
        (amount_cents, customer_id),
    )
    conn.execute(
        """
        INSERT INTO payments (customer_id, amount_cents, method, note, created_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (customer_id, -amount_cents, method, note or "credit repayment", created_by),
    )
    audit(conn, created_by, "credit_payment", "customer", customer_id,
          f"{amount_cents} via {method}")


def statement(customer_id, limit=100):
    """Every entry behind a customer's balance, newest first."""
    return query_all(
        """
        SELECT p.*, s.receipt_number, u.name AS user_name
        FROM payments p
        LEFT JOIN sales s ON s.id = p.sale_id
        LEFT JOIN users u ON u.id = p.created_by
        WHERE p.customer_id = ?
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ?
        """,
        (customer_id, limit),
    )
