"""
Product pairings - what customers actually buy together.

Two kinds of pairing live here, and they are deliberately separate:

  * Discovered pairings are counted from the sales that have already
    happened. Nobody types them in; they come out of the receipts.
  * Pinned pairings are ones the owner has decided on herself, which
    survive regardless of what the counts say.

This is the section a shopkeeper can collapse and never look at again.
It is a merchandising aid for the owner, not something the till needs.
"""

from ..db import audit, query_all, query_one

# Below this many receipts together, a pairing is coincidence rather than
# a pattern, and suggesting it would just be noise.
MIN_SUPPORT = 3


def discover(*, days=90, limit=20, min_support=MIN_SUPPORT):
    """
    Products that keep appearing on the same receipt.

    `together` is how many receipts held both. `confidence` is how often
    buying the first led to buying the second - the number that decides
    whether it is worth standing them next to each other, since a pair
    can be frequent simply because both items are popular on their own.
    """
    return query_all(
        """
        WITH lines AS (
            -- One row per product per receipt. DISTINCT because FEFO can
            -- split one purchase across batches into several rows, and
            -- that must not count as buying the thing twice.
            SELECT DISTINCT si.sale_id, si.product_id
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            WHERE s.status = 'completed'
              AND s.created_at >= datetime('now', ?)
        ),
        pairs AS (
            SELECT a.product_id AS product_a,
                   b.product_id AS product_b,
                   COUNT(*)     AS together
            FROM lines a
            JOIN lines b ON b.sale_id = a.sale_id AND b.product_id > a.product_id
            GROUP BY a.product_id, b.product_id
            HAVING together >= ?
        ),
        totals AS (
            SELECT product_id, COUNT(*) AS receipts FROM lines GROUP BY product_id
        )
        SELECT p.product_a, p.product_b, p.together,
               pa.name AS name_a, pb.name AS name_b,
               pa.stock_quantity AS stock_a, pb.stock_quantity AS stock_b,
               pa.retail_price_cents AS price_a, pb.retail_price_cents AS price_b,
               ta.receipts AS receipts_a, tb.receipts AS receipts_b,
               CAST(p.together AS REAL) / ta.receipts AS confidence,
               EXISTS (SELECT 1 FROM pinned_pairings pp
                       WHERE pp.product_a = p.product_a
                         AND pp.product_b = p.product_b) AS pinned
        FROM pairs p
        JOIN products pa ON pa.id = p.product_a
        JOIN products pb ON pb.id = p.product_b
        JOIN totals   ta ON ta.product_id = p.product_a
        JOIN totals   tb ON tb.product_id = p.product_b
        WHERE pa.active = 1 AND pb.active = 1
        ORDER BY p.together DESC, confidence DESC
        LIMIT ?
        """,
        (f"-{int(days)} days", min_support, limit),
    )


def suggestions_for(product_id, limit=3, days=90, min_support=MIN_SUPPORT):
    """
    What else to reach for when this product is being sold.

    Pinned pairings come first, then whatever the receipts say. Used by
    the till to prompt the person serving - "she usually takes bread with
    that" - which is the whole point of knowing any of this.
    """
    return query_all(
        """
        WITH lines AS (
            SELECT DISTINCT si.sale_id, si.product_id
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            WHERE s.status = 'completed'
              AND s.created_at >= datetime('now', ?)
        ),
        partners AS (
            SELECT other.product_id, COUNT(*) AS together
            FROM lines mine
            JOIN lines other ON other.sale_id = mine.sale_id
                            AND other.product_id <> mine.product_id
            WHERE mine.product_id = ?
            GROUP BY other.product_id
            HAVING together >= ?
        )
        SELECT p.id, p.name, p.retail_price_cents, p.stock_quantity, p.image_path,
               COALESCE(pt.together, 0) AS together,
               EXISTS (SELECT 1 FROM pinned_pairings pp
                       WHERE (pp.product_a = ? AND pp.product_b = p.id)
                          OR (pp.product_b = ? AND pp.product_a = p.id)) AS pinned
        FROM products p
        LEFT JOIN partners pt ON pt.product_id = p.id
        WHERE p.active = 1
          AND p.id <> ?
          AND p.stock_quantity > 0
          AND (pt.together IS NOT NULL OR pinned)
        ORDER BY pinned DESC, together DESC
        LIMIT ?
        """,
        (f"-{int(days)} days", product_id, min_support,
         product_id, product_id, product_id, limit),
    )


def list_pinned():
    return query_all(
        """
        SELECT pp.*, pa.name AS name_a, pb.name AS name_b,
               pa.retail_price_cents AS price_a, pb.retail_price_cents AS price_b,
               pa.stock_quantity AS stock_a, pb.stock_quantity AS stock_b,
               u.name AS pinned_by_name
        FROM pinned_pairings pp
        JOIN products pa ON pa.id = pp.product_a
        JOIN products pb ON pb.id = pp.product_b
        LEFT JOIN users u ON u.id = pp.created_by
        ORDER BY pp.created_at DESC
        """
    )


def pin(conn, product_a, product_b, *, created_by, note=None):
    """
    Records a pairing the owner wants kept regardless of the counts.

    The ids are stored in a fixed order so that pinning (bread, milk) and
    (milk, bread) cannot both exist as separate rows.
    """
    product_a, product_b = int(product_a), int(product_b)
    if product_a == product_b:
        raise ValueError("A product cannot be paired with itself.")
    if product_a > product_b:
        product_a, product_b = product_b, product_a

    for product_id in (product_a, product_b):
        if conn.execute("SELECT 1 FROM products WHERE id = ?",
                        (product_id,)).fetchone() is None:
            raise ValueError("Product not found.")

    conn.execute(
        """
        INSERT INTO pinned_pairings (product_a, product_b, note, created_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (product_a, product_b) DO UPDATE SET note = excluded.note
        """,
        (product_a, product_b, note, created_by),
    )
    audit(conn, created_by, "pairing_pinned", "product", product_a,
          f"paired with {product_b}")


def unpin(conn, product_a, product_b, *, removed_by):
    product_a, product_b = int(product_a), int(product_b)
    if product_a > product_b:
        product_a, product_b = product_b, product_a

    conn.execute(
        "DELETE FROM pinned_pairings WHERE product_a = ? AND product_b = ?",
        (product_a, product_b),
    )
    audit(conn, removed_by, "pairing_unpinned", "product", product_a,
          f"unpaired from {product_b}")
