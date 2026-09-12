"""
Every page renders.

A template typo does not fail any of the service tests, so these walk the
whole app as a signed-in owner and again as a cashier. Cheap to run and
they catch the class of error that otherwise only shows up in the shop.
"""

import pytest

from app.db import transaction
from app.services import customers, offers, sales


@pytest.fixture
def shop(client, owner, cashier, make_product, make_customer):
    """A shop with enough history that every page has something to draw."""
    from datetime import date, timedelta
    from app.services import stock

    plain = make_product(name="Sugar 1kg", retail=15000, wholesale=14000,
                         cost=12000, stock_qty=40)
    perishable = make_product(name="Milk 500ml", retail=6000, wholesale=5500,
                              cost=4500, track_expiry=True)
    make_product(name="Salt", retail=5000, wholesale=4500, stock_qty=0)

    with transaction() as conn:
        # Two batches: one nearly expired, so the dashboard has an alert.
        for days, qty in ((2, 6), (25, 20)):
            stock.record_stock_in(
                conn, product_id=perishable["id"], quantity=qty,
                cost_price_cents=4500, created_by=owner["id"],
                expiry_date=(date.today() + timedelta(days=days)).isoformat(),
            )
        offers.create_offer(conn, product_id=perishable["id"],
                            offer_price_cents=4000, approved_by=owner["id"],
                            tier="urgent")

    customer = make_customer(name="Wanjiku")

    with transaction() as conn:
        cash_sale = sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": plain["id"], "quantity": 2},
                   {"product_id": perishable["id"], "quantity": 3}],
            payment_method="cash", amount_paid_cents=50000,
        )
        credit_sale = sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": plain["id"], "quantity": 1}],
            payment_method="credit", customer_id=customer["id"],
        )

    with transaction() as conn:
        sales.record_return(conn, sale_id=cash_sale, product_id=plain["id"],
                            quantity=1, reason="wrong item", restocked=True,
                            created_by=cashier["id"])

    return {
        "product": plain,
        "perishable": perishable,
        "customer": customer,
        "cash_sale": cash_sale,
        "credit_sale": credit_sale,
    }


OWNER_PAGES = [
    "/", "/sell", "/stock-in", "/products", "/add-product", "/sales",
    "/customers", "/reports/", "/reports/products", "/reports/credit",
    "/reports/daily.csv", "/admin/staff", "/admin/offers", "/admin/audit",
    "/change-pin",
]


@pytest.mark.parametrize("path", OWNER_PAGES)
def test_owner_pages_render(client, shop, path):
    client.post("/login", data={"name": "Owner", "pin": "482913"})
    response = client.get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_detail_pages_render(client, shop):
    client.post("/login", data={"name": "Owner", "pin": "482913"})

    for path in (
        f"/receipt/{shop['cash_sale']}",
        f"/receipt/{shop['credit_sale']}",
        f"/sales/{shop['cash_sale']}/return",
        f"/customers/{shop['customer']['id']}",
        f"/products/{shop['product']['id']}/edit",
        f"/products/{shop['product']['id']}/adjust",
    ):
        assert client.get(path).status_code == 200, path


CASHIER_PAGES = ["/", "/sell", "/stock-in", "/products", "/sales", "/customers"]


@pytest.mark.parametrize("path", CASHIER_PAGES)
def test_cashier_pages_render(client, shop, path):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    assert client.get(path).status_code == 200


def test_the_cashier_dashboard_hides_the_takings(client, shop):
    """A cashier should not see the day's money or the margins."""
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    body = client.get("/").get_data(as_text=True)

    assert "Profit today" not in body
    assert "Cash in drawer" not in body
    assert "Low stock" in body, "but they still get the operational half"


def test_the_owner_dashboard_shows_the_takings(client, shop):
    client.post("/login", data={"name": "Owner", "pin": "482913"})
    body = client.get("/").get_data(as_text=True)
    assert "Profit today" in body


def test_login_and_setup_pages_render(client):
    assert client.get("/setup").status_code == 200
    client.post("/setup", data={"name": "Owner", "pin": "482913",
                                "confirm_pin": "482913"})
    assert client.get("/login").status_code == 200


def test_unknown_page_renders_the_404(client, owner):
    client.post("/login", data={"name": "Owner", "pin": "482913"})
    assert client.get("/no-such-page").status_code == 404


def test_a_product_name_cannot_inject_markup(client, owner, make_product):
    """
    Product names are user input and reach several pages. Jinja escapes
    them; this makes sure nobody turns that off later.
    """
    make_product(name="<script>alert('x')</script>", retail=1000, wholesale=900)
    client.post("/login", data={"name": "Owner", "pin": "482913"})

    body = client.get("/products").get_data(as_text=True)
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body
