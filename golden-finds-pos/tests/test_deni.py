"""
The deni book: goods taken now, paid for later.
"""

from datetime import date, timedelta

import pytest

from app.db import query_all, query_one, transaction
from app.services import deni, offers, products, stock
from app.services.deni import DeniError
from app.services.stock import StockError


def _give(user, product, quantity=1, name="Mama Wanjiku", phone="0712345678", **kwargs):
    with transaction() as conn:
        return deni.record(conn, customer_name=name, phone=phone,
                           product_id=product["id"], quantity=quantity,
                           created_by=user["id"], **kwargs)


def _pay(user, phone, amount_cents, method="cash"):
    with transaction() as conn:
        return deni.record_payment(conn, phone=phone, amount_cents=amount_cents,
                                   method=method, received_by=user["id"])


# ---------------------------------------------------------- recording --

def test_deni_is_written_down_with_everything(make_product, cashier):
    product = make_product(name="Omo 1kg", retail=35000, wholesale=33000, stock_qty=10)
    deni_id = _give(cashier, product, 2)

    entry = query_one("SELECT * FROM deni WHERE id = ?", (deni_id,))
    assert entry["customer_name"] == "Mama Wanjiku"
    assert entry["phone"] == "0712345678"
    assert entry["quantity"] == 2
    assert entry["unit_price_cents"] == 35000
    assert entry["total_cents"] == 70000
    assert entry["taken_on"] == date.today().isoformat()
    assert entry["status"] == "open"
    assert entry["created_by"] == cashier["id"]


def test_the_goods_leave_the_shelf(make_product, cashier):
    product = make_product(stock_qty=10)
    _give(cashier, product, 3)

    assert products.get(product["id"])["stock_quantity"] == 7
    assert stock.find_discrepancies() == []


def test_deni_uses_the_same_prices_as_the_till(make_product, owner, cashier):
    product = make_product(retail=10000, wholesale=9000, min_qty=6, stock_qty=50)

    wholesale = query_one("SELECT * FROM deni WHERE id = ?", (_give(cashier, product, 6),))
    assert wholesale["unit_price_cents"] == 9000

    with transaction() as conn:
        offers.create_offer(conn, product_id=product["id"], offer_price_cents=7000,
                            approved_by=owner["id"])
    on_offer = query_one("SELECT * FROM deni WHERE id = ?", (_give(cashier, product, 1),))
    assert on_offer["unit_price_cents"] == 7000


@pytest.mark.parametrize("written", [
    "0712345678", "0712 345 678", "+254712345678", "254 712 345 678", "712345678",
])
def test_a_phone_number_is_understood_however_it_is_written(written):
    assert deni.normalise_phone(written) == "0712345678"


def test_newer_01_numbers_are_accepted():
    assert deni.normalise_phone("0112 345 678") == "0112345678"


@pytest.mark.parametrize("bad", ["", "12345", "0812345678", "07123456789", "hello"])
def test_a_bad_phone_number_is_refused(bad):
    with pytest.raises(DeniError, match="phone number"):
        deni.normalise_phone(bad)


def test_a_name_is_required(make_product, cashier):
    product = make_product(stock_qty=5)
    with pytest.raises(DeniError, match="name"):
        _give(cashier, product, name="   ")


def test_the_day_can_be_earlier_but_not_in_the_future(make_product, cashier):
    product = make_product(stock_qty=10)
    last_week = (date.today() - timedelta(days=7)).isoformat()
    entry = query_one("SELECT * FROM deni WHERE id = ?",
                      (_give(cashier, product, taken_on=last_week),))
    assert entry["taken_on"] == last_week

    with pytest.raises(DeniError, match="future"):
        _give(cashier, product, taken_on=(date.today() + timedelta(days=1)).isoformat())


def test_deni_is_refused_when_the_stock_is_not_there(make_product, cashier):
    product = make_product(stock_qty=2)
    with pytest.raises(StockError):
        _give(cashier, product, 5)

    assert query_one("SELECT COUNT(*) AS n FROM deni")["n"] == 0, "rolled back"
    assert products.get(product["id"])["stock_quantity"] == 2


# ------------------------------------------------------------ paying --

def test_payment_clears_the_oldest_items_first(make_product, cashier):
    product = make_product(retail=10000, wholesale=9000, stock_qty=20)
    older = _give(cashier, product, 1, taken_on=(date.today() - timedelta(days=10)).isoformat())
    newer = _give(cashier, product, 1)

    left = _pay(cashier, "0712345678", 10000)

    assert left == 10000
    assert query_one("SELECT status FROM deni WHERE id = ?", (older,))["status"] == "paid"
    assert query_one("SELECT status FROM deni WHERE id = ?", (newer,))["status"] == "open"


def test_part_payment_is_tracked(make_product, cashier):
    product = make_product(retail=10000, wholesale=9000, stock_qty=20)
    deni_id = _give(cashier, product, 1)
    _pay(cashier, "0712345678", 4000, method="mpesa")

    entry = query_one("SELECT * FROM deni WHERE id = ?", (deni_id,))
    assert entry["paid_cents"] == 4000
    assert entry["status"] == "open"
    payment = query_one("SELECT * FROM deni_payments")
    assert payment["method"] == "mpesa"
    assert payment["received_by"] == cashier["id"]


def test_a_payment_finds_the_person_whatever_format_the_number_is_in(make_product, cashier):
    product = make_product(retail=10000, wholesale=9000, stock_qty=5)
    _give(cashier, product, 1, phone="0712 345 678")
    assert _pay(cashier, "+254712345678", 10000) == 0


def test_paying_more_than_is_owed_is_refused(make_product, cashier):
    product = make_product(retail=10000, wholesale=9000, stock_qty=5)
    _give(cashier, product, 1)
    with pytest.raises(DeniError, match="only owe"):
        _pay(cashier, "0712345678", 20000)


def test_a_number_that_owes_nothing(cashier):
    with pytest.raises(DeniError, match="doesn't owe"):
        _pay(cashier, "0799999999", 5000)


# -------------------------------------------------------- cancelling --

def test_cancelling_puts_the_item_back_and_keeps_the_record(make_product, owner, cashier):
    product = make_product(stock_qty=10)
    deni_id = _give(cashier, product, 4)

    with transaction() as conn:
        deni.cancel(conn, deni_id, cancelled_by=owner["id"], reason="wrong person")

    entry = query_one("SELECT * FROM deni WHERE id = ?", (deni_id,))
    assert entry["status"] == "cancelled"
    assert entry["cancel_reason"] == "wrong person"
    assert products.get(product["id"])["stock_quantity"] == 10
    assert stock.find_discrepancies() == []


def test_cancelling_expiring_stock_returns_it_to_the_right_batches(make_product, owner, cashier):
    product = make_product(track_expiry=True)
    with transaction() as conn:
        for days, qty in ((3, 2), (40, 10)):
            stock.record_stock_in(
                conn, product_id=product["id"], quantity=qty, cost_price_cents=5000,
                created_by=owner["id"],
                expiry_date=(date.today() + timedelta(days=days)).isoformat(),
            )

    deni_id = _give(cashier, product, 5)  # takes both batches
    with transaction() as conn:
        deni.cancel(conn, deni_id, cancelled_by=owner["id"], reason="mistake")

    batches = [row["quantity_remaining"] for row in query_all(
        "SELECT quantity_remaining FROM batches WHERE product_id = ? ORDER BY expiry_date",
        (product["id"],))]
    assert batches == [2, 10]
    assert stock.find_discrepancies() == []


def test_a_part_paid_deni_cannot_be_cancelled(make_product, owner, cashier):
    product = make_product(retail=10000, wholesale=9000, stock_qty=5)
    deni_id = _give(cashier, product, 1)
    _pay(cashier, "0712345678", 1000)

    with pytest.raises(DeniError, match="already been paid"):
        with transaction() as conn:
            deni.cancel(conn, deni_id, cancelled_by=owner["id"], reason="x")


def test_cancelling_needs_a_reason(make_product, owner, cashier):
    product = make_product(stock_qty=5)
    deni_id = _give(cashier, product, 1)
    with pytest.raises(DeniError, match="why"):
        with transaction() as conn:
            deni.cancel(conn, deni_id, cancelled_by=owner["id"], reason=" ")


# -------------------------------------------------------- the lists --

def test_who_owes_is_grouped_by_phone(make_product, cashier):
    product = make_product(retail=10000, wholesale=9000, stock_qty=20)
    _give(cashier, product, 1, name="Wanjiku", phone="0712345678")
    _give(cashier, product, 2, name="Wanjiku", phone="+254712345678")
    _give(cashier, product, 1, name="Otieno", phone="0722000111")

    people = {row["customer_name"]: row for row in deni.debtors()}
    assert people["Wanjiku"]["owed_cents"] == 30000
    assert people["Wanjiku"]["item_count"] == 2
    assert people["Otieno"]["owed_cents"] == 10000

    summary = deni.summary()
    assert summary["owed_cents"] == 40000
    assert summary["people"] == 2


# ------------------------------------------------------------ routes --

def _form(product, **overrides):
    data = {"customer_name": "Mama Wanjiku", "phone": "0712 345 678",
            "product_id": str(product["id"]), "quantity": "2"}
    data.update(overrides)
    return data


def test_a_shopkeeper_can_record_deni(client, owner, cashier, make_product):
    product = make_product(stock_qty=10)
    client.post("/login", data={"name": "Amina", "pin": "573820"})

    response = client.post("/deni/", data=_form(product))
    assert response.status_code == 302
    with client.application.app_context():
        entry = query_one("SELECT * FROM deni")
    assert entry["created_by"] == cashier["id"]


def test_a_mistake_in_the_form_is_shown_and_nothing_is_saved(client, owner, make_product):
    product = make_product(stock_qty=10)
    client.post("/login", data={"name": "Owner", "pin": "482913"})

    response = client.post("/deni/", data=_form(product, phone="123"))
    assert response.status_code == 400
    assert "Kenyan phone number" in response.get_data(as_text=True)
    with client.application.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM deni")["n"] == 0


def test_a_shopkeeper_can_take_a_repayment(client, owner, cashier, make_product):
    product = make_product(retail=10000, wholesale=9000, stock_qty=10)
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    client.post("/deni/", data=_form(product, quantity="1"))

    client.post("/deni/pay", data={"phone": "0712345678", "amount": "100", "method": "cash"})
    with client.application.app_context():
        assert query_one("SELECT status FROM deni")["status"] == "paid"


def test_a_shopkeeper_cannot_cancel_deni(client, owner, cashier, make_product):
    product = make_product(stock_qty=10)
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    client.post("/deni/", data=_form(product))
    with client.application.app_context():
        deni_id = query_one("SELECT id FROM deni")["id"]

    response = client.post(f"/deni/{deni_id}/cancel", data={"reason": "x"})
    assert response.status_code in (302, 403)
    with client.application.app_context():
        assert query_one("SELECT status FROM deni")["status"] == "open"


def test_the_person_page_shows_their_book(client, owner, make_product):
    product = make_product(name="Sugar 2kg", stock_qty=10)
    client.post("/login", data={"name": "Owner", "pin": "482913"})
    client.post("/deni/", data=_form(product))

    body = client.get("/deni/person/0712345678").get_data(as_text=True)
    assert "Mama Wanjiku" in body
    assert "Sugar 2kg" in body
    assert "0712 345 678" in body
    assert client.get("/deni/person/0799999999").status_code == 404
