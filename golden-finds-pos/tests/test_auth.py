"""
Authentication and authorisation.

The original app had none of this: every sale was anonymous and
/api/offer would let anyone on the network reprice any product.
"""

import pytest

from app.db import query_one, transaction
from app.security import AuthError, authenticate, hash_pin, validate_pin
from app.services import users


# ------------------------------------------------------------------ PINs --

def test_pin_is_hashed_not_stored(ctx):
    with transaction() as conn:
        user_id = users.create(conn, name="Test", pin="482913", role="cashier")
    row = query_one("SELECT pin_hash FROM users WHERE id = ?", (user_id,))
    assert "482913" not in row["pin_hash"]
    assert row["pin_hash"].startswith("scrypt:")


@pytest.mark.parametrize("bad", ["123", "abcd", "1111", "1234", "0000", ""])
def test_weak_or_malformed_pins_are_refused(bad):
    with pytest.raises(AuthError):
        validate_pin(bad)


def test_a_good_pin_is_accepted():
    assert validate_pin("482913") == "482913"


# -------------------------------------------------------------- SIGN IN --

def test_correct_pin_signs_in(owner):
    assert authenticate("Owner", "482913")["id"] == owner["id"]


def test_name_is_case_insensitive(owner):
    assert authenticate("owner", "482913")["id"] == owner["id"]


def test_wrong_pin_is_refused(owner):
    with pytest.raises(AuthError, match="incorrect"):
        authenticate("Owner", "999999")


def test_unknown_user_gives_the_same_message_as_a_wrong_pin(owner):
    """Otherwise the login form tells an attacker which names exist."""
    with pytest.raises(AuthError, match="incorrect"):
        authenticate("Nobody", "482913")


def test_deactivated_account_cannot_sign_in(owner, ctx):
    with transaction() as conn:
        user_id = users.create(conn, name="Gone", pin="573820", role="cashier")
        users.set_active(conn, user_id, False, changed_by=owner["id"])

    with pytest.raises(AuthError, match="deactivated"):
        authenticate("Gone", "573820")


def test_repeated_wrong_pins_lock_the_account(app, owner):
    with app.app_context():
        for _ in range(app.config["MAX_PIN_ATTEMPTS"]):
            with pytest.raises(AuthError):
                authenticate("Owner", "111222")

        with pytest.raises(AuthError, match="Too many wrong PINs"):
            authenticate("Owner", "482913")  # even the correct PIN is refused


def test_failed_logins_are_audited(owner, ctx):
    with pytest.raises(AuthError):
        authenticate("Owner", "999999")
    assert query_one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE action = 'login_failed'"
    )["n"] == 1


# -------------------------------------------------------- ROUTE GUARDING --

def _login(client, name, pin):
    return client.post("/login", data={"name": name, "pin": pin})


@pytest.mark.parametrize("path", [
    "/", "/sell", "/stock-in", "/products", "/sales", "/reports/",
    "/admin/staff", "/admin/audit",
])
def test_pages_require_a_login(client, owner, path):
    response = client.get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("path", ["/api/search?q=xx", "/api/product/123"])
def test_api_requires_a_login(client, owner, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.get_json()["success"] is False


def test_anonymous_cannot_create_an_offer(client, owner, make_product):
    """The original hole: an unauthenticated POST could set any price."""
    product = make_product(retail=10000)
    response = client.post("/api/offer", json={
        "product_id": product["id"], "offer_price": 1,
    })
    assert response.status_code == 401
    assert query_one("SELECT COUNT(*) AS n FROM offers")["n"] == 0


def test_cashier_cannot_create_an_offer(client, owner, cashier, make_product):
    product = make_product(retail=10000)
    _login(client, "Amina", "573820")

    response = client.post("/api/offer", json={
        "product_id": product["id"], "offer_price": 50,
    })
    assert response.status_code == 403
    assert query_one("SELECT COUNT(*) AS n FROM offers")["n"] == 0


def test_owner_can_create_an_offer(client, owner, make_product):
    product = make_product(retail=10000)
    _login(client, "Owner", "482913")

    response = client.post("/api/offer", json={
        "product_id": product["id"], "offer_price": 60,
    })
    assert response.status_code == 200
    offer = query_one("SELECT * FROM offers")
    assert offer["offer_price_cents"] == 6000
    assert offer["approved_by"] == owner["id"], "the approver is recorded"


def test_cashier_cannot_reach_reports(client, owner, cashier):
    _login(client, "Amina", "573820")
    assert client.get("/reports/").status_code in (302, 403)


def test_sale_records_who_made_it(client, owner, cashier, make_product):
    product = make_product(stock_qty=10)
    _login(client, "Amina", "573820")

    response = client.post("/api/checkout", json={
        "items": [{"product_id": product["id"], "quantity": 1}],
        "payment_method": "cash",
    })
    assert response.status_code == 200

    sale = query_one("SELECT * FROM sales")
    assert sale["cashier_id"] == cashier["id"], "no more anonymous sales"


def test_logout_clears_the_session(client, owner):
    _login(client, "Owner", "482913")
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/").status_code == 302


# ------------------------------------------------------ ACCOUNT SAFETY --

def test_the_last_owner_cannot_be_deactivated(owner, ctx):
    with pytest.raises(AuthError, match="last owner"):
        with transaction() as conn:
            users.set_active(conn, owner["id"], False, changed_by=owner["id"])


def test_the_last_owner_cannot_be_demoted(owner, ctx):
    with pytest.raises(AuthError, match="last owner"):
        with transaction() as conn:
            users.set_role(conn, owner["id"], "cashier", changed_by=owner["id"])


def test_duplicate_names_are_refused(owner, ctx):
    with pytest.raises(AuthError, match="already uses that name"):
        with transaction() as conn:
            users.create(conn, name="owner", pin="573820", role="cashier")


# ------------------------------------------------------------- FIRST RUN --

def test_setup_creates_the_first_owner_then_closes(client):
    assert client.get("/").status_code == 302

    response = client.post("/setup", data={
        "name": "Shop Owner", "pin": "482913", "confirm_pin": "482913",
    })
    assert response.status_code == 302

    with client.application.app_context():
        assert query_one("SELECT role FROM users")["role"] == "admin"

    # Now sealed: it must not mint a second owner.
    client.post("/setup", data={
        "name": "Impostor", "pin": "573820", "confirm_pin": "573820",
    })
    with client.application.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM users")["n"] == 1


def test_setup_rejects_mismatched_pins(client):
    client.post("/setup", data={
        "name": "Owner", "pin": "482913", "confirm_pin": "999888",
    })
    with client.application.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM users")["n"] == 0
