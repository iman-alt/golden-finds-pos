"""
Stock movement, FEFO, and ledger integrity.
"""

from datetime import date, timedelta

import pytest

from app.db import query_one, transaction
from app.services import products, sales, stock
from app.services.stock import StockError


def _expiry(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_stock_in_increases_quantity_and_writes_the_ledger(make_product, owner):
    product = make_product()
    with transaction() as conn:
        stock.record_stock_in(conn, product_id=product["id"], quantity=20,
                              cost_price_cents=8000, created_by=owner["id"])

    assert products.get(product["id"])["stock_quantity"] == 20
    movement = query_one(
        "SELECT * FROM stock_movements WHERE product_id = ?", (product["id"],)
    )
    assert movement["quantity_change"] == 20
    assert movement["movement_type"] == "stock_in"
    assert movement["created_by"] == owner["id"]


def test_expiry_tracked_stock_in_requires_a_date(make_product, owner):
    product = make_product(track_expiry=True)
    with pytest.raises(StockError, match="expiry date"):
        with transaction() as conn:
            stock.record_stock_in(conn, product_id=product["id"], quantity=5,
                                  cost_price_cents=8000, created_by=owner["id"])


def test_fefo_consumes_the_soonest_expiring_batch_first(make_product, owner, cashier):
    product = make_product(track_expiry=True)
    with transaction() as conn:
        # Received in the "wrong" order on purpose: the later delivery
        # expires sooner and must therefore go out first.
        stock.record_stock_in(conn, product_id=product["id"], quantity=10,
                              cost_price_cents=8000, created_by=owner["id"],
                              expiry_date=_expiry(60))
        stock.record_stock_in(conn, product_id=product["id"], quantity=10,
                              cost_price_cents=8500, created_by=owner["id"],
                              expiry_date=_expiry(5))

    with transaction() as conn:
        sales.record_sale(conn, cashier_id=cashier["id"],
                          items=[{"product_id": product["id"], "quantity": 4}],
                          payment_method="cash")

    batches = query_one(
        "SELECT quantity_remaining FROM batches WHERE product_id = ? "
        "ORDER BY expiry_date LIMIT 1", (product["id"],)
    )
    assert batches["quantity_remaining"] == 6, "sold from the soonest-expiring batch"


def test_fefo_splits_across_batches(make_product, owner, cashier):
    product = make_product(track_expiry=True)
    with transaction() as conn:
        stock.record_stock_in(conn, product_id=product["id"], quantity=3,
                              cost_price_cents=8000, created_by=owner["id"],
                              expiry_date=_expiry(5))
        stock.record_stock_in(conn, product_id=product["id"], quantity=10,
                              cost_price_cents=8500, created_by=owner["id"],
                              expiry_date=_expiry(30))

    with transaction() as conn:
        sale_id = sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": product["id"], "quantity": 5}],
            payment_method="cash",
        )

    from app.db import query_all
    items = query_all(
        "SELECT * FROM sale_items WHERE sale_id = ? ORDER BY id", (sale_id,)
    )
    assert [i["quantity"] for i in items] == [3, 2], "split across two batches"
    assert products.get(product["id"])["stock_quantity"] == 8


def test_insufficient_batch_stock_raises_instead_of_drifting(make_product, owner, cashier):
    """
    The original code decremented the product total even when the batches
    could not cover the sale, leaving stock_quantity and the batches
    permanently out of step. It must refuse the sale instead.
    """
    product = make_product(track_expiry=True)
    with transaction() as conn:
        stock.record_stock_in(conn, product_id=product["id"], quantity=3,
                              cost_price_cents=8000, created_by=owner["id"],
                              expiry_date=_expiry(10))
        # Force the cached total out of step, the way a crash or a bad
        # manual edit would.
        conn.execute("UPDATE products SET stock_quantity = 50 WHERE id = ?",
                     (product["id"],))

    with pytest.raises(StockError, match="Not enough stock"):
        with transaction() as conn:
            sales.record_sale(conn, cashier_id=cashier["id"],
                              items=[{"product_id": product["id"], "quantity": 10}],
                              payment_method="cash")

    # And nothing was taken from the batches.
    row = query_one(
        "SELECT SUM(quantity_remaining) AS n FROM batches WHERE product_id = ?",
        (product["id"],)
    )
    assert row["n"] == 3


def test_overselling_a_simple_product_is_refused(make_product, cashier):
    product = make_product(stock_qty=3)
    with pytest.raises(StockError, match="Not enough stock"):
        with transaction() as conn:
            sales.record_sale(conn, cashier_id=cashier["id"],
                              items=[{"product_id": product["id"], "quantity": 4}],
                              payment_method="cash")


def test_a_failed_line_rolls_the_whole_sale_back(make_product, cashier):
    """One bad line must leave no trace of the sale at all."""
    plenty = make_product(name="Rice", stock_qty=20)
    scarce = make_product(name="Milk", stock_qty=1)

    with pytest.raises(StockError):
        with transaction() as conn:
            sales.record_sale(
                conn, cashier_id=cashier["id"],
                items=[
                    {"product_id": plenty["id"], "quantity": 2},
                    {"product_id": scarce["id"], "quantity": 5},
                ],
                payment_method="cash",
            )

    assert products.get(plenty["id"])["stock_quantity"] == 20, "not deducted"
    assert query_one("SELECT COUNT(*) AS n FROM sales")["n"] == 0
    assert query_one("SELECT COUNT(*) AS n FROM sale_items")["n"] == 0


def test_ledger_and_cache_agree_after_normal_trading(make_product, owner, cashier):
    product = make_product(stock_qty=100)
    with transaction() as conn:
        for _ in range(5):
            sales.record_sale(conn, cashier_id=cashier["id"],
                              items=[{"product_id": product["id"], "quantity": 3}],
                              payment_method="cash")

    assert products.get(product["id"])["stock_quantity"] == 85
    assert stock.find_discrepancies() == []


def test_recalculate_repairs_a_drifted_cache(make_product, owner):
    product = make_product(stock_qty=40)
    with transaction() as conn:
        conn.execute("UPDATE products SET stock_quantity = 999 WHERE id = ?",
                     (product["id"],))

    assert stock.find_discrepancies(), "the drift is detected"

    with transaction() as conn:
        stock.recalculate_stock(conn, product["id"], created_by=owner["id"])

    assert products.get(product["id"])["stock_quantity"] == 40


def test_adjustment_logs_the_difference(make_product, owner):
    product = make_product(stock_qty=20)
    with transaction() as conn:
        difference = stock.adjust(conn, product_id=product["id"], new_quantity=17,
                                  reason="damaged", created_by=owner["id"])

    assert difference == -3
    movement = query_one(
        "SELECT * FROM stock_movements WHERE movement_type = 'damaged'"
    )
    assert movement["quantity_change"] == -3


def test_write_off_clears_an_expired_batch(make_product, owner):
    product = make_product(track_expiry=True)
    with transaction() as conn:
        stock.record_stock_in(conn, product_id=product["id"], quantity=12,
                              cost_price_cents=8000, created_by=owner["id"],
                              expiry_date=_expiry(-1))
    batch = query_one("SELECT id FROM batches WHERE product_id = ?", (product["id"],))

    with transaction() as conn:
        written_off = stock.write_off_expired(conn, batch_id=batch["id"],
                                              created_by=owner["id"])

    assert written_off == 12
    assert products.get(product["id"])["stock_quantity"] == 0


def test_low_stock_orders_out_of_stock_first(make_product):
    make_product(name="Plenty", stock_qty=100, threshold=5)
    make_product(name="Empty", stock_qty=0, threshold=5)
    make_product(name="Low", stock_qty=2, threshold=5)

    names = [row["name"] for row in stock.get_low_stock()]
    assert names[0] == "Empty"
    assert "Plenty" not in names
