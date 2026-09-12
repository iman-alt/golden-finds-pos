"""
Pricing and the offer-at-checkout bug.

The original code showed the offer price in the cart but recorded retail,
because record_sale never consulted the offers table. These tests exist
to make sure that can never come back.
"""

from datetime import datetime, timedelta

import pytest

from app.db import query_one, transaction
from app.services import offers, sales
from app.services.sales import SaleError


def test_retail_price_below_wholesale_threshold(make_product):
    product = make_product(retail=10000, wholesale=9000, min_qty=6, stock_qty=50)
    price, basis, offer_id = sales.resolve_price(product, 5)
    assert (price, basis, offer_id) == (10000, "retail", None)


def test_wholesale_price_at_threshold(make_product):
    product = make_product(retail=10000, wholesale=9000, min_qty=6, stock_qty=50)
    price, basis, _ = sales.resolve_price(product, 6)
    assert (price, basis) == (9000, "wholesale")


def test_offer_beats_both_retail_and_wholesale(make_product, owner):
    product = make_product(retail=10000, wholesale=9000, min_qty=6, stock_qty=50)
    with transaction() as conn:
        offers.create_offer(conn, product_id=product["id"],
                            offer_price_cents=6000, approved_by=owner["id"])

    offer = offers.get_active_offer(product["id"])
    assert sales.resolve_price(product, 1, offer)[:2] == (6000, "offer")
    assert sales.resolve_price(product, 10, offer)[:2] == (6000, "offer")


def test_sale_records_the_offer_price_not_retail(make_product, owner, cashier):
    """The regression test for the original money bug."""
    product = make_product(retail=10000, wholesale=9000, stock_qty=20)
    with transaction() as conn:
        offers.create_offer(conn, product_id=product["id"],
                            offer_price_cents=6000, approved_by=owner["id"])

    with transaction() as conn:
        sale_id = sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": product["id"], "quantity": 2}],
            payment_method="cash",
        )

    sale = sales.get_sale(sale_id)
    assert sale["total_cents"] == 12000, "customer was quoted the offer price"

    item = query_one("SELECT * FROM sale_items WHERE sale_id = ?", (sale_id,))
    assert item["unit_price_cents"] == 6000
    assert item["price_basis"] == "offer"


def test_cart_preview_matches_recorded_sale(make_product, owner, cashier):
    """
    Whatever the screen shows must be what the database records. Both go
    through price_cart, so this asserts they cannot drift apart.
    """
    product = make_product(retail=10000, wholesale=9000, min_qty=6, stock_qty=50)
    with transaction() as conn:
        offers.create_offer(conn, product_id=product["id"],
                            offer_price_cents=7500, approved_by=owner["id"])

    items = [{"product_id": product["id"], "quantity": 8}]
    _, previewed_total = sales.price_cart(items)

    with transaction() as conn:
        sale_id = sales.record_sale(conn, cashier_id=cashier["id"], items=items,
                                    payment_method="cash")

    assert sales.get_sale(sale_id)["total_cents"] == previewed_total


def test_expired_offer_stops_applying(make_product, owner):
    """An offer with a past end_date must not keep discounting forever."""
    product = make_product(retail=10000, stock_qty=10)
    past = (datetime.now() - timedelta(days=1)).isoformat(sep=" ", timespec="seconds")

    with transaction() as conn:
        offers.create_offer(conn, product_id=product["id"],
                            offer_price_cents=6000, approved_by=owner["id"],
                            end_date=past)

    assert offers.get_active_offer(product["id"]) is None

    _, subtotal = sales.price_cart([{"product_id": product["id"], "quantity": 1}])
    assert subtotal == 10000, "price fell back to retail once the offer ended"


def test_future_end_date_still_applies(make_product, owner):
    product = make_product(retail=10000, stock_qty=10)
    future = (datetime.now() + timedelta(days=3)).isoformat(sep=" ", timespec="seconds")

    with transaction() as conn:
        offers.create_offer(conn, product_id=product["id"],
                            offer_price_cents=6000, approved_by=owner["id"],
                            end_date=future)

    assert offers.get_active_offer(product["id"])["offer_price_cents"] == 6000


def test_offer_above_retail_is_refused(make_product, owner):
    product = make_product(retail=10000)
    with pytest.raises(ValueError, match="below the retail price"):
        with transaction() as conn:
            offers.create_offer(conn, product_id=product["id"],
                                offer_price_cents=12000, approved_by=owner["id"])


def test_creating_an_offer_supersedes_the_previous_one(make_product, owner):
    product = make_product(retail=10000)
    with transaction() as conn:
        offers.create_offer(conn, product_id=product["id"],
                            offer_price_cents=8000, approved_by=owner["id"])
        offers.create_offer(conn, product_id=product["id"],
                            offer_price_cents=7000, approved_by=owner["id"])

    active = offers.list_active_offers()
    assert len(active) == 1
    assert active[0]["offer_price_cents"] == 7000


def test_price_is_never_taken_from_the_client(make_product, cashier):
    """
    A cart item carrying its own price must be ignored entirely - the
    server prices from the database or not at all.
    """
    product = make_product(retail=10000, stock_qty=10)
    with transaction() as conn:
        sale_id = sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": product["id"], "quantity": 1,
                    "unit_price_cents": 1, "price": 0.01}],
            payment_method="cash",
        )
    assert sales.get_sale(sale_id)["total_cents"] == 10000


def test_duplicate_product_in_cart_is_rejected(make_product):
    product = make_product(stock_qty=10)
    with pytest.raises(SaleError, match="twice"):
        sales.price_cart([
            {"product_id": product["id"], "quantity": 1},
            {"product_id": product["id"], "quantity": 1},
        ])


def test_zero_or_negative_quantity_is_rejected(make_product):
    product = make_product(stock_qty=10)
    for quantity in (0, -3):
        with pytest.raises(SaleError):
            sales.price_cart([{"product_id": product["id"], "quantity": quantity}])
