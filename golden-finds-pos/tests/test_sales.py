"""
The sale lifecycle: payment, receipts, voids, returns and credit.

None of this existed in the original - the tables were there but no code
touched them.
"""

import pytest

from app.db import query_one, transaction
from app.services import customers, products, sales
from app.services.sales import SaleError


def _sell(cashier, product, quantity=1, **kwargs):
    with transaction() as conn:
        return sales.record_sale(
            conn, cashier_id=cashier["id"],
            items=[{"product_id": product["id"], "quantity": quantity}],
            payment_method=kwargs.pop("payment_method", "cash"), **kwargs,
        )


# ------------------------------------------------------------- PAYMENT --

def test_change_is_calculated_from_cash_given(make_product, cashier):
    product = make_product(retail=7500, wholesale=7000, stock_qty=10)
    sale_id = _sell(cashier, product, 1, amount_paid_cents=10000)

    sale = sales.get_sale(sale_id)
    assert sale["total_cents"] == 7500
    assert sale["amount_paid_cents"] == 10000
    assert sale["change_cents"] == 2500


def test_underpayment_is_refused(make_product, cashier):
    product = make_product(retail=10000, stock_qty=10)
    with pytest.raises(SaleError, match="less than the total"):
        _sell(cashier, product, 1, amount_paid_cents=5000)


def test_exact_payment_is_assumed_when_none_is_given(make_product, cashier):
    product = make_product(retail=10000, stock_qty=10)
    sale = sales.get_sale(_sell(cashier, product, 1))
    assert sale["amount_paid_cents"] == 10000
    assert sale["change_cents"] == 0


def test_receipt_numbers_are_unique_and_readable(make_product, cashier):
    product = make_product(stock_qty=50)
    numbers = [sales.get_sale(_sell(cashier, product))["receipt_number"]
               for _ in range(3)]

    assert len(set(numbers)) == 3
    assert numbers[0].startswith("GF-")
    assert numbers[0].endswith("0001")
    assert numbers[2].endswith("0003")


def test_an_invalid_payment_method_is_refused(make_product, cashier):
    product = make_product(stock_qty=10)
    with pytest.raises(SaleError, match="valid payment method"):
        _sell(cashier, product, 1, payment_method="barter")


def test_cost_is_frozen_onto_the_sale_line(make_product, owner, cashier):
    """
    A later cost change must not rewrite the margin on sales already made.
    """
    product = make_product(retail=10000, cost=6000, stock_qty=10)
    sale_id = _sell(cashier, product, 2)

    with transaction() as conn:
        products.update(conn, product["id"], updated_by=owner["id"],
                        cost_price_cents=9500)

    item = query_one("SELECT * FROM sale_items WHERE sale_id = ?", (sale_id,))
    assert item["unit_cost_cents"] == 6000, "the old cost still stands"


# --------------------------------------------------------------- VOIDS --

def test_voiding_returns_the_stock(make_product, owner, cashier):
    product = make_product(stock_qty=20)
    sale_id = _sell(cashier, product, 5)
    assert products.get(product["id"])["stock_quantity"] == 15

    with transaction() as conn:
        sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="wrong item")

    assert products.get(product["id"])["stock_quantity"] == 20
    assert sales.get_sale(sale_id)["status"] == "voided"


def test_a_voided_sale_is_kept_not_deleted(make_product, owner, cashier):
    """A till that can make transactions vanish can be stolen from."""
    product = make_product(stock_qty=10)
    sale_id = _sell(cashier, product, 1)

    with transaction() as conn:
        sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="mistake")

    sale = sales.get_sale(sale_id)
    assert sale is not None
    assert sale["voided_by"] == owner["id"]
    assert sale["void_reason"] == "mistake"
    assert query_one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE action = 'sale_voided'"
    )["n"] == 1


def test_voiding_needs_a_reason(make_product, owner, cashier):
    product = make_product(stock_qty=10)
    sale_id = _sell(cashier, product, 1)
    with pytest.raises(SaleError, match="reason"):
        with transaction() as conn:
            sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="  ")


def test_a_sale_cannot_be_voided_twice(make_product, owner, cashier):
    product = make_product(stock_qty=10)
    sale_id = _sell(cashier, product, 2)

    with transaction() as conn:
        sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="a")
    with pytest.raises(SaleError, match="already voided"):
        with transaction() as conn:
            sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="b")

    assert products.get(product["id"])["stock_quantity"] == 10, "stock returned once"


# -------------------------------------------------------------- RETURNS --

def test_a_return_restocks_and_refunds(make_product, cashier):
    product = make_product(retail=10000, stock_qty=20)
    sale_id = _sell(cashier, product, 5)

    with transaction() as conn:
        refunded = sales.record_return(
            conn, sale_id=sale_id, product_id=product["id"], quantity=2,
            reason="wrong size", restocked=True, created_by=cashier["id"],
        )

    assert refunded == 20000
    assert products.get(product["id"])["stock_quantity"] == 17


def test_damaged_goods_are_not_restocked(make_product, cashier):
    product = make_product(retail=10000, stock_qty=20)
    sale_id = _sell(cashier, product, 5)

    with transaction() as conn:
        sales.record_return(
            conn, sale_id=sale_id, product_id=product["id"], quantity=2,
            reason="broken", restocked=False, created_by=cashier["id"],
        )

    assert products.get(product["id"])["stock_quantity"] == 15, "stayed sold"


def test_refund_uses_the_price_paid_not_the_current_price(make_product, owner, cashier):
    product = make_product(retail=10000, stock_qty=20)
    sale_id = _sell(cashier, product, 1)

    with transaction() as conn:
        products.update(conn, product["id"], updated_by=owner["id"],
                        retail_price_cents=15000)

    with transaction() as conn:
        refunded = sales.record_return(
            conn, sale_id=sale_id, product_id=product["id"], quantity=1,
            reason="changed mind", restocked=True, created_by=cashier["id"],
        )

    assert refunded == 10000, "refunded what the customer actually paid"


def test_cannot_return_more_than_was_bought(make_product, cashier):
    product = make_product(stock_qty=20)
    sale_id = _sell(cashier, product, 2)

    with pytest.raises(SaleError, match="Only 2"):
        with transaction() as conn:
            sales.record_return(
                conn, sale_id=sale_id, product_id=product["id"], quantity=3,
                reason="x", restocked=True, created_by=cashier["id"],
            )


def test_returns_accumulate_up_to_the_quantity_sold(make_product, cashier):
    product = make_product(stock_qty=20)
    sale_id = _sell(cashier, product, 3)

    for _ in range(3):
        with transaction() as conn:
            sales.record_return(
                conn, sale_id=sale_id, product_id=product["id"], quantity=1,
                reason="x", restocked=True, created_by=cashier["id"],
            )

    with pytest.raises(SaleError, match="fully returned"):
        with transaction() as conn:
            sales.record_return(
                conn, sale_id=sale_id, product_id=product["id"], quantity=1,
                reason="x", restocked=True, created_by=cashier["id"],
            )


def test_a_voided_sale_cannot_also_be_returned(make_product, owner, cashier):
    product = make_product(stock_qty=10)
    sale_id = _sell(cashier, product, 2)

    with transaction() as conn:
        sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="x")

    with pytest.raises(SaleError, match="voided"):
        with transaction() as conn:
            sales.record_return(
                conn, sale_id=sale_id, product_id=product["id"], quantity=1,
                reason="x", restocked=True, created_by=cashier["id"],
            )


def test_a_sale_with_returns_cannot_be_voided(make_product, owner, cashier):
    """Otherwise the stock would come back twice."""
    product = make_product(stock_qty=10)
    sale_id = _sell(cashier, product, 3)

    with transaction() as conn:
        sales.record_return(
            conn, sale_id=sale_id, product_id=product["id"], quantity=1,
            reason="x", restocked=True, created_by=cashier["id"],
        )

    with pytest.raises(SaleError, match="already has returns"):
        with transaction() as conn:
            sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="x")


# --------------------------------------------------------------- CREDIT --

def test_a_credit_sale_raises_the_customer_balance(make_product, make_customer, cashier):
    product = make_product(retail=10000, stock_qty=10)
    customer = make_customer()

    _sell(cashier, product, 3, payment_method="credit", customer_id=customer["id"])

    assert customers.get(customer["id"])["credit_balance_cents"] == 30000


def test_credit_needs_a_customer(make_product, cashier):
    product = make_product(stock_qty=10)
    with pytest.raises(SaleError, match="customer account"):
        _sell(cashier, product, 1, payment_method="credit")


def test_the_credit_limit_is_enforced(make_product, make_customer, cashier):
    product = make_product(retail=10000, stock_qty=50)
    customer = make_customer(credit_limit_cents=25000)

    _sell(cashier, product, 2, payment_method="credit", customer_id=customer["id"])

    with pytest.raises(SaleError, match="credit limit"):
        _sell(cashier, product, 1, payment_method="credit",
              customer_id=customer["id"])


def test_a_repayment_lowers_the_balance(make_product, make_customer, cashier):
    product = make_product(retail=10000, stock_qty=10)
    customer = make_customer()
    _sell(cashier, product, 2, payment_method="credit", customer_id=customer["id"])

    with transaction() as conn:
        customers.record_payment(conn, customer_id=customer["id"],
                                 amount_cents=15000, method="mpesa",
                                 created_by=cashier["id"])

    assert customers.get(customer["id"])["credit_balance_cents"] == 5000


def test_overpayment_is_refused(make_product, make_customer, cashier):
    from app.services.customers import CustomerError

    customer = make_customer()
    with pytest.raises(CustomerError, match="only owes"):
        with transaction() as conn:
            customers.record_payment(conn, customer_id=customer["id"],
                                     amount_cents=5000, method="cash",
                                     created_by=cashier["id"])


def test_voiding_a_credit_sale_clears_the_debt(make_product, make_customer,
                                               owner, cashier):
    product = make_product(retail=10000, stock_qty=10)
    customer = make_customer()
    sale_id = _sell(cashier, product, 2, payment_method="credit",
                    customer_id=customer["id"])

    with transaction() as conn:
        sales.void_sale(conn, sale_id, voided_by=owner["id"], reason="error")

    assert customers.get(customer["id"])["credit_balance_cents"] == 0


# ------------------------------------------------------------- RECEIPTS --

def test_a_split_batch_line_shows_as_one_line_on_the_receipt(make_product, owner,
                                                             cashier):
    """
    FEFO may consume two batches for one cart line, but the customer
    bought one thing and the receipt should say so.
    """
    from datetime import date, timedelta
    from app.services import stock

    product = make_product(track_expiry=True, retail=10000)
    with transaction() as conn:
        for days, qty in ((5, 3), (30, 10)):
            stock.record_stock_in(
                conn, product_id=product["id"], quantity=qty,
                cost_price_cents=8000, created_by=owner["id"],
                expiry_date=(date.today() + timedelta(days=days)).isoformat(),
            )

    sale_id = _sell(cashier, product, 5)

    assert query_one(
        "SELECT COUNT(*) AS n FROM sale_items WHERE sale_id = ?", (sale_id,)
    )["n"] == 2, "two batches were consumed"

    lines = sales.get_sale_items(sale_id)
    assert len(lines) == 1, "but the receipt shows one line"
    assert lines[0]["quantity"] == 5
    assert lines[0]["line_total_cents"] == 50000
