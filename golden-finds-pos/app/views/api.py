"""
JSON endpoints used by the sell and stock-in screens.

Every one of these is behind a login, and the ones that change anything
are behind a role check. The original `/api/offer` had neither, which
meant anyone who could reach the machine on the network could set any
price on any product.
"""

from flask import Blueprint, jsonify, request

from ..db import transaction
from ..money import MoneyError, format_money, parse_money
from ..security import admin_required, current_user, login_required
from ..services import offers, products, sales, stock
from ..services.icons import icon_for
from ..services.images import image_url
from ..timeutil import to_local
from ..services.products import ProductError
from ..services.sales import SaleError
from ..services.stock import StockError

bp = Blueprint("api", __name__, url_prefix="/api")


def _fail(message, status=400):
    return jsonify({"success": False, "message": message}), status


@bp.get("/product/<barcode>")
@login_required
def get_product(barcode):
    product = products.get_by_barcode(barcode)
    if product is None:
        return jsonify({"found": False}), 404

    offer = offers.get_active_offer(product["id"])
    return jsonify(products.to_json(product, offer))


@bp.get("/search")
@login_required
def search():
    results = products.search(request.args.get("q", ""))
    return jsonify([
        {
            "id": p["id"],
            "name": p["name"],
            "icon": icon_for(p["name"], p["category"]),
            "image_url": image_url(p["image_path"]),
            "barcode": p["barcode"],
            "retail_price_cents": p["retail_price_cents"],
            "retail_price_display": format_money(p["retail_price_cents"]),
            "stock_quantity": p["stock_quantity"],
            "unit_type": p["unit_type"],
        }
        for p in results
    ])


@bp.post("/cart/price")
@login_required
def price_cart():
    """
    Prices the cart on the server so the screen shows exactly what will be
    charged. The browser keeps no prices of its own - it asks for them.
    """
    data = request.get_json(silent=True) or {}
    try:
        lines, subtotal = sales.price_cart(data.get("items", []))
    except SaleError as err:
        return _fail(str(err))

    return jsonify({
        "success": True,
        "subtotal_cents": subtotal,
        "subtotal_display": format_money(subtotal),
        "lines": [
            {
                "product_id": line["product_id"],
                "name": line["name"],
                "icon": icon_for(line["name"], line["product"]["category"]),
                "image_url": image_url(line["product"]["image_path"]),
                "quantity": line["quantity"],
                "unit_price_cents": line["unit_price_cents"],
                "unit_price_display": format_money(line["unit_price_cents"]),
                "line_total_cents": line["line_total_cents"],
                "line_total_display": format_money(line["line_total_cents"]),
                "price_basis": line["price_basis"],
                "stock_quantity": line["product"]["stock_quantity"],
            }
            for line in lines
        ],
    })


@bp.post("/checkout")
@login_required
def checkout():
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not items:
        return _fail("Cart is empty.")

    amount_paid = data.get("amount_paid")
    try:
        paid_cents = (
            parse_money(amount_paid, field="Amount paid", allow_zero=True)
            if amount_paid not in (None, "") else None
        )
    except MoneyError as err:
        return _fail(str(err))

    try:
        with transaction() as conn:
            sale_id = sales.record_sale(
                conn,
                cashier_id=current_user()["id"],
                items=items,
                payment_method=data.get("payment_method", "cash"),
                amount_paid_cents=paid_cents,
            )
    except (SaleError, StockError) as err:
        return _fail(str(err))

    sale = sales.get_sale(sale_id)
    return jsonify({
        "success": True,
        "sale_id": sale_id,
        "receipt_number": sale["receipt_number"],
        "total_cents": sale["total_cents"],
        "total_display": format_money(sale["total_cents"]),
        "change_cents": sale["change_cents"],
        "change_display": format_money(sale["change_cents"]),
        "receipt_url": f"/receipt/{sale_id}",
    })


@bp.post("/stock-in")
@admin_required
def stock_in():
    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id"))
        quantity = int(data.get("quantity"))
    except (TypeError, ValueError):
        return _fail("Enter a valid quantity.")

    try:
        cost_cents = parse_money(data.get("cost_price"), field="Cost price")
    except MoneyError as err:
        return _fail(str(err))

    try:
        with transaction() as conn:
            stock.record_stock_in(
                conn,
                product_id=product_id,
                quantity=quantity,
                cost_price_cents=cost_cents,
                created_by=current_user()["id"],
                expiry_date=data.get("expiry_date") or None,
                batch_number=data.get("batch_number") or None,
                supplier_id=data.get("supplier_id") or None,
            )
    except StockError as err:
        return _fail(str(err))

    product = products.get(product_id)
    return jsonify({
        "success": True,
        "message": f"Added {quantity} × {product['name']}.",
        "stock_quantity": product["stock_quantity"],
    })


@bp.get("/stock-in/recent")
@admin_required
def stock_in_recent():
    """
    Deliveries logged in the last day, newest first - the list on the
    stock-in screen that confirms a scan was actually saved.
    """
    from ..db import query_all

    rows = query_all(
        """
        SELECT m.id, m.quantity_change, m.created_at,
               p.name, p.category, p.image_path, b.expiry_date, u.name AS user_name
        FROM stock_movements m
        JOIN products p ON p.id = m.product_id
        LEFT JOIN batches b ON b.id = m.batch_id
        LEFT JOIN users u ON u.id = m.created_by
        WHERE m.movement_type = 'stock_in'
          AND m.created_at >= datetime('now', '-1 day')
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT 50
        """
    )
    return jsonify([
        {
            "id": row["id"],
            "product_name": row["name"],
            "icon": icon_for(row["name"], row["category"]),
            "image_url": image_url(row["image_path"]),
            "quantity": row["quantity_change"],
            "expiry_date": row["expiry_date"],
            "user_name": row["user_name"],
            "time": to_local(row["created_at"])[11:16],
        }
        for row in rows
    ])


@bp.post("/offer")
@admin_required
def create_offer():
    """Owner-only. This is the endpoint that used to be wide open."""
    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id"))
    except (TypeError, ValueError):
        return _fail("Invalid product.")

    try:
        price_cents = parse_money(data.get("offer_price"), field="Offer price")
    except MoneyError as err:
        return _fail(str(err))

    batch_id = data.get("batch_id")
    try:
        with transaction() as conn:
            offer_id = offers.create_offer(
                conn,
                product_id=product_id,
                offer_price_cents=price_cents,
                approved_by=current_user()["id"],
                batch_id=int(batch_id) if batch_id else None,
                tier=data.get("tier"),
                end_date=data.get("end_date") or None,
            )
    except ValueError as err:
        return _fail(str(err))

    return jsonify({
        "success": True,
        "offer_id": offer_id,
        "message": f"Offer set at {format_money(price_cents)}.",
    })


@bp.post("/offer/<int:offer_id>/end")
@admin_required
def end_offer(offer_id):
    with transaction() as conn:
        offers.end_offer(conn, offer_id, ended_by=current_user()["id"])
    return jsonify({"success": True, "message": "Offer ended."})


@bp.post("/batch/<int:batch_id>/write-off")
@admin_required
def write_off(batch_id):
    data = request.get_json(silent=True) or {}
    try:
        with transaction() as conn:
            quantity = stock.write_off_expired(
                conn, batch_id=batch_id,
                created_by=current_user()["id"],
                note=data.get("note") or "expired",
            )
    except StockError as err:
        return _fail(str(err))

    return jsonify({
        "success": True,
        "message": f"Wrote off {quantity} expired units.",
    })


@bp.get("/pairings/<int:product_id>")
@login_required
def pairing_suggestions(product_id):
    """
    What usually goes with this, for the prompt on the till. Available to
    whoever is serving - suggesting a second item is selling, not
    editing.
    """
    from ..services import pairings

    return jsonify([
        {
            "id": row["id"],
            "name": row["name"],
            "icon": icon_for(row["name"]),
            "image_url": image_url(row["image_path"]),
            "price_display": format_money(row["retail_price_cents"]),
            "stock_quantity": row["stock_quantity"],
            "pinned": bool(row["pinned"]),
        }
        for row in pairings.suggestions_for(product_id)
    ])
