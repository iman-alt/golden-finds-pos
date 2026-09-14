"""
Quick items: short number codes for things without a barcode.
"""

import pytest

from app.db import query_one
from app.services import quick_items
from app.services.quick_items import QuickItemError


def _login_owner(client):
    client.post("/login", data={"name": "Owner", "pin": "482913"})


def _form(**overrides):
    data = {"group": "2", "code": "222", "name": "Hair pins", "price": "20",
            "cost_price": "10", "stock": "50", "unit_type": "piece"}
    data.update(overrides)
    return data


# --------------------------------------------------------------- codes --

@pytest.mark.parametrize("code", ["1", "111", "2201", "999999"])
def test_short_numbers_are_quick_codes(code):
    assert quick_items.is_quick_code(code)


@pytest.mark.parametrize("code", ["6161100821234", "12345678", "11a", "", "1234567"])
def test_real_barcodes_and_letters_are_not(code):
    assert not quick_items.is_quick_code(code)


def test_a_bad_code_is_explained():
    with pytest.raises(QuickItemError, match="numbers only"):
        quick_items.validate_code("milk")


def test_the_next_free_code_skips_used_ones(ctx, make_product):
    assert quick_items.next_free_code(1) == "111"
    make_product(barcode="111", retail=6000, wholesale=6000)
    make_product(barcode="112", retail=6000, wholesale=6000)
    assert quick_items.next_free_code(1) == "113"
    assert quick_items.next_free_code(2) == "211"


def test_items_are_grouped_by_first_digit(ctx, make_product):
    make_product(name="Milk", barcode="111", retail=6000, wholesale=6000)
    make_product(name="Hair pins", barcode="222", retail=2000, wholesale=2000)
    make_product(name="Omo", barcode="6161100821234", retail=35000, wholesale=33000)

    groups = {g["label"]: [i["name"] for i in g["items"]] for g in quick_items.grouped_items()}
    assert groups == {"Dairy & drinks": ["Milk"], "Hair & beauty": ["Hair pins"]}


# -------------------------------------------------------------- create --

def test_the_owner_can_create_a_quick_item_with_stock(app, client, owner):
    _login_owner(client)
    response = client.post("/quick-items/", data=_form())
    assert response.status_code == 302
    assert "group=2" in response.headers["Location"], "back to the form for the next one"

    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '222'")
    assert product["name"] == "Hair pins"
    assert product["retail_price_cents"] == 2000
    assert product["wholesale_price_cents"] == 2000
    assert product["category"] == "Personal Care & Cosmetics"
    assert product["stock_quantity"] == 50


def test_creating_several_in_a_row(app, client, owner):
    _login_owner(client)
    for code, name in (("211", "Hair pins"), ("212", "Hair bands"), ("213", "Comb")):
        assert client.post("/quick-items/", data=_form(code=code, name=name)).status_code == 302
    with app.app_context():
        assert quick_items.next_free_code(2) == "214"
        assert len(quick_items.list_items()) == 3


def test_a_code_already_in_use_is_refused(app, client, owner, make_product):
    make_product(name="Milk", barcode="222", retail=6000, wholesale=6000)
    _login_owner(client)
    response = client.post("/quick-items/", data=_form())
    assert response.status_code == 400
    assert "already registered" in response.get_data(as_text=True)


def test_a_code_with_letters_is_refused(client, owner):
    _login_owner(client)
    response = client.post("/quick-items/", data=_form(code="HP1"))
    assert response.status_code == 400


def test_cost_and_stock_are_optional(app, client, owner):
    _login_owner(client)
    response = client.post("/quick-items/", data=_form(cost_price="", stock=""))
    assert response.status_code == 302
    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '222'")
    assert product["cost_price_cents"] == 0
    assert product["stock_quantity"] == 0


def test_a_shopkeeper_can_see_the_codes_but_not_create(app, client, owner, cashier, make_product):
    make_product(name="Milk", barcode="111", retail=6000, wholesale=6000)
    client.post("/login", data={"name": "Amina", "pin": "573820"})

    page = client.get("/quick-items/")
    assert page.status_code == 200
    assert "Milk" in page.get_data(as_text=True)
    assert "Create a quick item" not in page.get_data(as_text=True)

    assert client.post("/quick-items/", data=_form()).status_code in (302, 403)
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM products WHERE barcode = '222'")["n"] == 0


# ---------------------------------------------------------- at the till --

def test_typing_the_code_finds_the_item(client, owner, make_product):
    make_product(name="Milk 500ml", barcode="111", retail=6000, wholesale=6000, stock_qty=10)
    _login_owner(client)
    data = client.get("/api/product/111").get_json()
    assert data["name"] == "Milk 500ml"


def test_the_till_tiles_list_only_quick_items(client, owner, make_product):
    make_product(name="Milk", barcode="111", retail=6000, wholesale=6000)
    make_product(name="Omo", barcode="6161100821234", retail=35000, wholesale=33000)
    _login_owner(client)

    tiles = client.get("/quick-items/api").get_json()
    assert [t["name"] for t in tiles] == ["Milk"]
    assert tiles[0]["code"] == "111"
    assert tiles[0]["group"] == "Dairy & drinks"


def test_the_print_list_renders(client, owner, make_product):
    make_product(name="Hair pins", barcode="222", retail=2000, wholesale=2000)
    _login_owner(client)
    body = client.get("/quick-items/print").get_data(as_text=True)
    assert "222" in body and "Hair pins" in body


# ------------------------------------------------- group and code must agree --

def test_a_code_from_another_group_is_refused(app, client, owner):
    """A hair pin saved as 111 would show under Dairy & drinks."""
    _login_owner(client)
    response = client.post("/quick-items/", data=_form(group="2", code="111"))
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Dairy &amp; drinks code" in body
    assert "use 211" in body
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM products")["n"] == 0


def test_a_group_must_be_chosen(client, owner):
    _login_owner(client)
    data = _form()
    del data["group"]
    response = client.post("/quick-items/", data=data)
    assert response.status_code == 400
    assert "Choose which group" in response.get_data(as_text=True)


def test_a_wrong_code_can_be_fixed_on_the_edit_page(app, client, owner, make_product):
    product = make_product(name="hair pin", barcode="111", retail=5000, wholesale=5000, stock_qty=10)
    _login_owner(client)
    response = client.post(f"/products/{product['id']}/edit", data={
        "barcode": "211", "name": "hair pin", "category": "Personal Care & Cosmetics",
        "unit_type": "piece", "cost_price": "30", "retail_price": "50", "wholesale_price": "50",
        "wholesale_min_qty": "6", "low_stock_threshold": "5", "active": "on",
    })
    assert response.status_code == 302

    with app.app_context():
        fixed = query_one("SELECT * FROM products WHERE id = ?", (product["id"],))
        assert fixed["barcode"] == "211"
        assert fixed["stock_quantity"] == 10, "stock stays with the product"
        groups = {g["label"]: [i["name"] for i in g["items"]] for g in quick_items.grouped_items()}
        assert groups == {"Hair & beauty": ["hair pin"]}


def test_changing_to_a_code_already_in_use_is_refused(app, client, owner, make_product):
    make_product(name="Milk", barcode="111", retail=6000, wholesale=6000)
    pins = make_product(name="hair pin", barcode="211", retail=5000, wholesale=5000)
    _login_owner(client)
    client.post(f"/products/{pins['id']}/edit", data={
        "barcode": "111", "name": "hair pin", "category": "Personal Care & Cosmetics",
        "unit_type": "piece", "cost_price": "30", "retail_price": "50", "wholesale_price": "50",
        "wholesale_min_qty": "6", "low_stock_threshold": "5", "active": "on",
    })
    with app.app_context():
        assert query_one("SELECT barcode FROM products WHERE id = ?", (pins["id"],))["barcode"] == "211"
