from flask import Flask, render_template, request, jsonify, redirect, url_for
from database import init_db
from models import (
    get_product_by_barcode, search_products, record_sale,
    get_active_offer, get_low_stock_products, add_product, record_stock_in,
    get_expiry_alerts, create_offer
)

app = Flask(__name__)

# Ensures all tables exist the moment the app starts - safe to call
# every time, won't touch existing data.
init_db()


@app.route("/")
def home():
    return render_template(
        "dashboard.html",
        low_stock=get_low_stock_products(),
        expiry_alerts=get_expiry_alerts()
    )


@app.route("/api/offer", methods=["POST"])
def api_create_offer():
    """
    Called when the admin picks a price from the offer picker.
    Expects JSON: { "product_id": 1, "batch_id": 3, "offer_price": 48, "tier": "urgent" }
    """
    data = request.get_json()
    try:
        product_id = int(data.get("product_id"))
        batch_id = data.get("batch_id")
        offer_price = float(data.get("offer_price"))
        tier = data.get("tier")
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid offer data."}), 400

    if offer_price <= 0:
        return jsonify({"success": False, "message": "Offer price must be greater than zero."}), 400

    create_offer(
        product_id=product_id,
        offer_price=offer_price,
        tier=tier,
        approved_by=None,  # will be replaced once login/roles are built
        batch_id=batch_id
    )
    return jsonify({"success": True, "message": "Offer created."})


@app.route("/sell")
def sell():
    return render_template("sell.html")


@app.route("/add-product", methods=["GET", "POST"])
def add_product_route():
    if request.method == "GET":
        barcode = request.args.get("barcode", "")
        return render_template("add_product.html", barcode=barcode, error=None)

    # POST - form submitted
    form = request.form
    barcode = form.get("barcode", "").strip()
    name = form.get("name", "").strip()
    category = form.get("category", "").strip()
    unit_type = form.get("unit_type", "piece")
    track_expiry = form.get("track_expiry") == "on"

    try:
        retail_price = float(form.get("retail_price", 0))
        wholesale_price = float(form.get("wholesale_price", 0))
        wholesale_min_qty = int(form.get("wholesale_min_qty", 6))
        cost_price = float(form.get("cost_price", 0))
        low_stock_threshold = int(form.get("low_stock_threshold", 5))
    except ValueError:
        return render_template(
            "add_product.html", barcode=barcode,
            error="Prices and quantities must be numbers."
        )

    if not barcode or not name:
        return render_template(
            "add_product.html", barcode=barcode,
            error="Barcode and name are required."
        )

    success, message = add_product(
        barcode=barcode, name=name, category=category, unit_type=unit_type,
        retail_price=retail_price, wholesale_price=wholesale_price,
        wholesale_min_qty=wholesale_min_qty, cost_price=cost_price,
        low_stock_threshold=low_stock_threshold, track_expiry=track_expiry
    )

    if not success:
        return render_template("add_product.html", barcode=barcode, error=message)

    # Send them back to Sell so they can immediately continue the transaction
    return redirect(url_for("sell", added=barcode))


@app.route("/api/product/<barcode>")
def api_get_product(barcode):
    """
    Called by the scanner input on every scan. Returns product details
    as JSON, plus any active offer price, or a 404 if the barcode
    isn't registered yet.
    """
    product = get_product_by_barcode(barcode)
    if product is None:
        return jsonify({"found": False}), 404

    offer = get_active_offer(product["id"])

    return jsonify({
        "found": True,
        "id": product["id"],
        "name": product["name"],
        "barcode": product["barcode"],
        "retail_price": product["retail_price"],
        "wholesale_price": product["wholesale_price"],
        "wholesale_min_qty": product["wholesale_min_qty"],
        "stock_quantity": product["stock_quantity"],
        "track_expiry": bool(product["track_expiry"]),
        "offer_price": offer["offer_price"] if offer else None
    })


@app.route("/api/search")
def api_search():
    """
    Called as the cashier types in the search box (e.g. "coke").
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])

    results = search_products(query)
    return jsonify([{
        "id": p["id"],
        "name": p["name"],
        "barcode": p["barcode"],
        "retail_price": p["retail_price"],
        "stock_quantity": p["stock_quantity"]
    } for p in results])


@app.route("/stock-in")
def stock_in():
    return render_template("stock_in.html")


@app.route("/api/stock-in", methods=["POST"])
def api_stock_in():
    """
    Expects JSON: { "product_id": 1, "quantity": 20, "cost_price": 190,
                     "expiry_date": "2026-09-01" (optional) }
    """
    data = request.get_json()

    try:
        product_id = int(data.get("product_id"))
        quantity = int(data.get("quantity"))
        cost_price = float(data.get("cost_price"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid quantity or cost price."}), 400

    expiry_date = data.get("expiry_date") or None

    success, message = record_stock_in(
        product_id=product_id,
        quantity=quantity,
        cost_price=cost_price,
        created_by=None,  # will be replaced once login/roles are built
        expiry_date=expiry_date
    )

    status_code = 200 if success else 400
    return jsonify({"success": success, "message": message}), status_code


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    """
    Receives the finished cart and payment method, records the sale.
    Expects JSON: { "items": [{"product_id": 1, "quantity": 2}, ...],
                    "payment_method": "cash" }
    """
    data = request.get_json()
    items = data.get("items", [])
    payment_method = data.get("payment_method", "cash")

    if not items:
        return jsonify({"success": False, "message": "Cart is empty."}), 400

    success, message, sale_id = record_sale(
        cashier_id=None,  # will be replaced once login/roles are built
        cart_items=items,
        payment_method=payment_method
    )

    status_code = 200 if success else 400
    return jsonify({"success": success, "message": message, "sale_id": sale_id}), status_code


if __name__ == "__main__":
    app.run(debug=True)
