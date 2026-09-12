import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.config import Config
from app.db import transaction
from app.services import products, stock, users


@pytest.fixture(autouse=True)
def _clear_lockouts():
    """
    Lockout counters are keyed by user id and live in the process, not the
    database. Each test gets a fresh database that restarts ids at 1, so
    without this a lockout from one test would follow a different user
    into the next.
    """
    from app.security import reset_attempts
    reset_attempts()
    yield
    reset_attempts()


@pytest.fixture
def app():
    """
    A fresh app on a throwaway database file.

    A file rather than :memory: because the app opens one connection per
    request - separate in-memory connections would each get their own
    empty database and nothing would persist between requests.
    """
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)

    application = create_app(Config, DATABASE=path, TESTING=True,
                             SECRET_KEY="test-key", MAX_PIN_ATTEMPTS=5,
                             UPLOAD_DIR=tempfile.mkdtemp(prefix="gf-photos-"))
    yield application

    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


@pytest.fixture
def owner(ctx):
    with transaction() as conn:
        user_id = users.create(conn, name="Owner", pin="482913", role="admin")
    return users.get(user_id)


@pytest.fixture
def cashier(ctx, owner):
    with transaction() as conn:
        user_id = users.create(conn, name="Amina", pin="573820", role="cashier")
    return users.get(user_id)


@pytest.fixture
def make_product(ctx, owner):
    """Creates a product and optionally stocks it, returning the row."""
    def _make(name="Sugar 1kg", barcode=None, retail=10000, wholesale=9000,
              min_qty=6, cost=8000, track_expiry=False, stock_qty=0,
              expiry_date=None, threshold=5):
        nonlocal counter
        counter += 1
        with transaction() as conn:
            product_id, _ = products.create(
                conn,
                barcode=barcode or f"BC{counter:06d}",
                name=name,
                category="Groceries & Food Items",
                unit_type="piece",
                retail_price_cents=retail,
                wholesale_price_cents=wholesale,
                wholesale_min_qty=min_qty,
                cost_price_cents=cost,
                low_stock_threshold=threshold,
                track_expiry=track_expiry,
                created_by=owner["id"],
            )
            if stock_qty:
                stock.record_stock_in(
                    conn, product_id=product_id, quantity=stock_qty,
                    cost_price_cents=cost, created_by=owner["id"],
                    expiry_date=expiry_date,
                )
        return products.get(product_id)

    counter = 0
    return _make


@pytest.fixture
def signed_in(client, app):
    """Signs a client in, returning a helper to switch users."""
    def _login(name, pin):
        return client.post("/login", data={"name": name, "pin": pin},
                           follow_redirects=False)
    return _login
