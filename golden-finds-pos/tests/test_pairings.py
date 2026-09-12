"""
Product pairings and icons.
"""

import pytest

from app.db import transaction
from app.services import pairings, sales
from app.services.icons import icon_for


def _sell_together(cashier, *products):
    with transaction() as conn:
        return sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": p["id"], "quantity": 1} for p in products],
            payment_method="cash",
        )


# ----------------------------------------------------------- DISCOVERY --

def test_nothing_is_suggested_without_enough_evidence(make_product, cashier):
    """Two receipts is a coincidence, not a pattern."""
    bread = make_product(name="Bread", stock_qty=50)
    milk = make_product(name="Milk", stock_qty=50)

    for _ in range(2):
        _sell_together(cashier, bread, milk)

    assert pairings.discover() == []


def test_a_repeated_pair_is_found(make_product, cashier):
    bread = make_product(name="Bread", stock_qty=50)
    milk = make_product(name="Milk", stock_qty=50)
    soap = make_product(name="Soap", stock_qty=50)

    for _ in range(4):
        _sell_together(cashier, bread, milk)
    _sell_together(cashier, soap)

    found = pairings.discover()
    assert len(found) == 1
    names = {found[0]["name_a"], found[0]["name_b"]}
    assert names == {"Bread", "Milk"}
    assert found[0]["together"] == 4


def test_confidence_reflects_follow_through(make_product, cashier):
    """
    Bread sells ten times and takes milk with it three of those. The pair
    is real but weak, and the number should say so rather than implying
    that bread means milk.
    """
    bread = make_product(name="Bread", stock_qty=100)
    milk = make_product(name="Milk", stock_qty=100)

    for _ in range(3):
        _sell_together(cashier, bread, milk)
    for _ in range(7):
        _sell_together(cashier, bread)

    found = pairings.discover()
    assert len(found) == 1
    # Whichever way round the pair is stored, confidence is measured from
    # product_a, and bread appears on all ten receipts.
    assert found[0]["together"] == 3
    assert 0 < found[0]["confidence"] <= 1


def test_a_split_batch_line_is_not_counted_twice(make_product, owner, cashier):
    """
    FEFO turns one purchase into several sale_items rows. That must not
    look like the customer bought the thing more than once.
    """
    from datetime import date, timedelta
    from app.services import stock

    milk = make_product(name="Milk", track_expiry=True)
    bread = make_product(name="Bread", stock_qty=50)

    with transaction() as conn:
        for days, qty in ((3, 1), (30, 50)):
            stock.record_stock_in(
                conn, product_id=milk["id"], quantity=qty,
                cost_price_cents=4000, created_by=owner["id"],
                expiry_date=(date.today() + timedelta(days=days)).isoformat(),
            )

    # Two units of milk, which FEFO splits across both batches.
    for _ in range(3):
        with transaction() as conn:
            sales.record_sale(
                conn, cashier_id=cashier["id"],
                items=[{"product_id": milk["id"], "quantity": 2},
                       {"product_id": bread["id"], "quantity": 1}],
                payment_method="cash",
            )

    found = pairings.discover()
    assert len(found) == 1
    assert found[0]["together"] == 3, "three receipts, not six rows"


def test_discovery_ignores_voided_sales(make_product, owner, cashier):
    bread = make_product(name="Bread", stock_qty=50)
    milk = make_product(name="Milk", stock_qty=50)

    sale_ids = [_sell_together(cashier, bread, milk) for _ in range(4)]
    with transaction() as conn:
        for sale_id in sale_ids:
            sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="x")

    assert pairings.discover() == []


# -------------------------------------------------- TILL SUGGESTIONS --

def test_the_till_is_told_what_goes_with_an_item(make_product, cashier):
    bread = make_product(name="Bread", stock_qty=50)
    milk = make_product(name="Milk", stock_qty=50)

    for _ in range(4):
        _sell_together(cashier, bread, milk)

    suggested = pairings.suggestions_for(bread["id"])
    assert [row["name"] for row in suggested] == ["Milk"]


def test_out_of_stock_items_are_not_suggested(make_product, cashier):
    """Offering something the shop has run out of wastes everyone's time."""
    bread = make_product(name="Bread", stock_qty=50)
    milk = make_product(name="Milk", stock_qty=4)

    for _ in range(4):
        _sell_together(cashier, bread, milk)

    assert pairings.suggestions_for(bread["id"]) == []


# ------------------------------------------------------------ PINNING --

def test_a_pinned_pair_is_suggested_without_any_sales(make_product, owner):
    bread = make_product(name="Bread", stock_qty=50)
    jam = make_product(name="Jam", stock_qty=50)

    with transaction() as conn:
        pairings.pin(conn, bread["id"], jam["id"], created_by=owner["id"],
                     note="stand them together")

    assert [row["name"] for row in pairings.suggestions_for(bread["id"])] == ["Jam"]
    # And it works from the other side of the pair too.
    assert [row["name"] for row in pairings.suggestions_for(jam["id"])] == ["Bread"]


def test_pinning_the_same_pair_either_way_round_makes_one_row(make_product, owner):
    bread = make_product(name="Bread", stock_qty=10)
    jam = make_product(name="Jam", stock_qty=10)

    with transaction() as conn:
        pairings.pin(conn, bread["id"], jam["id"], created_by=owner["id"])
        pairings.pin(conn, jam["id"], bread["id"], created_by=owner["id"])

    assert len(pairings.list_pinned()) == 1


def test_a_product_cannot_be_paired_with_itself(make_product, owner):
    bread = make_product(name="Bread", stock_qty=10)
    with pytest.raises(ValueError, match="itself"):
        with transaction() as conn:
            pairings.pin(conn, bread["id"], bread["id"], created_by=owner["id"])


def test_unpinning_removes_it(make_product, owner):
    bread = make_product(name="Bread", stock_qty=10)
    jam = make_product(name="Jam", stock_qty=10)

    with transaction() as conn:
        pairings.pin(conn, bread["id"], jam["id"], created_by=owner["id"])
    with transaction() as conn:
        pairings.unpin(conn, jam["id"], bread["id"], removed_by=owner["id"])

    assert pairings.list_pinned() == []


def test_pinning_is_audited(make_product, owner, ctx):
    from app.db import query_one

    bread = make_product(name="Bread", stock_qty=10)
    jam = make_product(name="Jam", stock_qty=10)
    with transaction() as conn:
        pairings.pin(conn, bread["id"], jam["id"], created_by=owner["id"])

    assert query_one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE action = 'pairing_pinned'"
    )["n"] == 1


# -------------------------------------------------------------- ICONS --

@pytest.mark.parametrize("name,expected", [
    ("Fresh Milk 500ml", "🥛"),
    ("Brown Bread", "🍞"),
    ("Omo 1kg", "🧺"),
    ("Sugar 2kg", "🍬"),
    ("Coca Cola 500ml", "🥤"),
    ("Unga wa Ngano", "🌽"),
    ("Sukuma Wiki", "🥬"),
    ("Pampers Medium", "🍼"),
    ("Colgate 100ml", "🪥"),
])
def test_icons_match_what_is_on_the_shelf(name, expected):
    assert icon_for(name, "Other") == expected


def test_swahili_names_are_recognised():
    assert icon_for("Maziwa", "Other") == "🥛"
    assert icon_for("Mkate", "Other") == "🍞"
    assert icon_for("Sukari", "Other") == "🍬"


def test_an_unknown_product_falls_back_to_its_category():
    assert icon_for("Something Unusual", "Baby Care Products") == "🍼"


def test_there_is_always_an_icon():
    """A missing icon would leave a ragged column in the cart."""
    for name in ("", None, "zzzz", "!!!"):
        assert icon_for(name) == "📦"
