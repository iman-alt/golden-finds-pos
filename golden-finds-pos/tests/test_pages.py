"""
Every page renders.

A template typo does not fail any of the service tests, so these walk the
whole app as a signed-in owner and again as a cashier. Cheap to run and
they catch the class of error that otherwise only shows up in the shop.
"""

import pytest

from app.db import transaction
from app.services import offers, sales


@pytest.fixture
def shop(client, owner, cashier, make_product):
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

    with transaction() as conn:
        cash_sale = sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": plain["id"], "quantity": 2},
                   {"product_id": perishable["id"], "quantity": 3}],
            payment_method="cash", amount_paid_cents=50000,
        )
        mpesa_sale = sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": plain["id"], "quantity": 1}],
            payment_method="mpesa",
        )

    with transaction() as conn:
        sales.record_return(conn, sale_id=cash_sale, product_id=plain["id"],
                            quantity=1, reason="wrong item", restocked=True,
                            created_by=cashier["id"])

    return {
        "product": plain,
        "perishable": perishable,
        "cash_sale": cash_sale,
        "mpesa_sale": mpesa_sale,
    }


OWNER_PAGES = [
    "/", "/sell", "/stock-in", "/products", "/add-product", "/sales",
    "/reports/", "/reports/products",
    "/reports/daily.csv", "/admin/staff", "/admin/offers", "/admin/audit",
    "/admin/pairings",
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
        f"/receipt/{shop['mpesa_sale']}",
        f"/sales/{shop['cash_sale']}/return",
        f"/products/{shop['product']['id']}/edit",
        f"/products/{shop['product']['id']}/adjust",
    ):
        assert client.get(path).status_code == 200, path


CASHIER_PAGES = ["/", "/sell", "/products", "/sales", "/my-day"]


@pytest.mark.parametrize("path", CASHIER_PAGES)
def test_cashier_pages_render(client, shop, path):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    assert client.get(path).status_code == 200


# A shopkeeper sells. They do not change data - not stock, not prices, not
# products, and not refunds. Each of these is a way to cover a shortfall.
CASHIER_FORBIDDEN = [
    "/stock-in",
    "/add-product",
    "/reports/",
    "/reports/products",
    "/admin/staff",
    "/admin/offers",
    "/admin/audit",
    "/admin/pairings",
]


@pytest.mark.parametrize("path", CASHIER_FORBIDDEN)
def test_a_shopkeeper_cannot_reach_data_pages(client, shop, path):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    assert client.get(path).status_code in (302, 403), path


def test_a_shopkeeper_cannot_edit_or_adjust_a_product(client, shop):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    product_id = shop["product"]["id"]

    for path in (f"/products/{product_id}/edit", f"/products/{product_id}/adjust"):
        assert client.get(path).status_code in (302, 403), path


def test_a_shopkeeper_cannot_receive_stock(client, shop):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    response = client.post("/api/stock-in", json={
        "product_id": shop["product"]["id"], "quantity": 500, "cost_price": "1",
    })
    assert response.status_code == 403

    with client.application.app_context():
        from app.services import products
        assert products.get(shop["product"]["id"])["stock_quantity"] != 500


def test_a_shopkeeper_cannot_refund_or_void(client, shop):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    sale_id = shop["cash_sale"]

    assert client.get(f"/sales/{sale_id}/return").status_code in (302, 403)
    assert client.post(f"/sales/{sale_id}/void",
                       data={"reason": "x"}).status_code in (302, 403)

    with client.application.app_context():
        from app.services import sales
        assert sales.get_sale(sale_id)["status"] == "completed"


def test_a_shopkeeper_can_still_sell(client, shop):
    """The lockdown must not get in the way of the actual job."""
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    response = client.post("/api/checkout", json={
        "items": [{"product_id": shop["product"]["id"], "quantity": 1}],
        "payment_method": "cash",
    })
    assert response.status_code == 200


def test_the_till_warns_both_roles_about_expiring_stock(client, shop):
    """
    The person handing goods over is the one who can push them, so the
    warning is not owner-only - but the offer button still is.
    """
    for name, pin in (("Amina", "573820"), ("Owner", "482913")):
        client.post("/login", data={"name": name, "pin": pin})
        body = client.get("/sell").get_data(as_text=True)
        assert "selling soon" in body, name
        client.post("/logout")


def test_my_day_shows_takings_but_not_margins(client, shop):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    body = client.get("/my-day").get_data(as_text=True)

    assert "Cash to hand over" in body
    assert "Profit" not in body
    assert "cost of goods" not in body


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


def test_credit_is_no_longer_offered(client, shop):
    client.post("/login", data={"name": "Owner", "pin": "482913"})
    body = client.get("/sell").get_data(as_text=True)
    assert "Credit" not in body
    assert client.get("/customers").status_code == 404
    response = client.post("/api/checkout", json={
        "items": [{"product_id": shop["product"]["id"], "quantity": 1}],
        "payment_method": "credit",
    })
    assert response.status_code == 400


def test_a_shopkeeper_sees_only_their_own_sales_for_today(client, shop, owner, make_product):
    """
    Even with dates or another cashier forced into the address, a
    shopkeeper's sales list is their own sales, today, and nothing else.
    """
    from app.db import transaction
    from app.services import sales as sales_service

    product = make_product(name="Owner Only Item", retail=9900, wholesale=9000, stock_qty=5)
    with transaction() as conn:
        owner_sale = sales_service.record_sale(
            conn, cashier_id=owner["id"],
            items=[{"product_id": product["id"], "quantity": 1}],
            payment_method="cash",
        )
        conn.execute(
            "UPDATE sales SET created_at = datetime('now', '-3 days') WHERE id = ?",
            (shop["mpesa_sale"],),
        )

    client.post("/login", data={"name": "Amina", "pin": "573820"})
    body = client.get(
        f"/sales?from=2000-01-01&to=2100-01-01&cashier_id={owner['id']}"
    ).get_data(as_text=True)

    with client.application.app_context():
        own_today = sales_service.get_sale(shop["cash_sale"])["receipt_number"]
        old = sales_service.get_sale(shop["mpesa_sale"])["receipt_number"]
        not_mine = sales_service.get_sale(owner_sale)["receipt_number"]

    assert own_today in body
    assert old not in body, "an earlier day is hidden"
    assert not_mine not in body, "someone else's sale is hidden"
    assert 'type="date"' not in body, "no date picker for a shopkeeper"
