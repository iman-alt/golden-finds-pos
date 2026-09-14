"""
Quick items: create, save and look up items that have no barcode.

Everyone can see the list and use the tiles at the till. Only the owner
creates items, the same as any other product.
"""

from flask import (
    Blueprint, flash, jsonify, redirect, render_template, request, url_for
)

from ..db import transaction
from ..money import MoneyError, format_money, parse_money
from ..security import admin_required, current_user, login_required
from ..services import images, products, quick_items, stock
from ..services.images import ImageError
from ..services.products import UNIT_TYPES, ProductError
from ..services.quick_items import GROUPS, GROUPS_BY_DIGIT, QuickItemError
from ..services.stock import StockError

bp = Blueprint("quick", __name__, url_prefix="/quick-items")


def _page(form=None, error=None, status=200):
    return render_template(
        "quick_items.html",
        groups=GROUPS,
        grouped=quick_items.grouped_items(),
        suggestions=quick_items.suggestions(),
        unit_types=UNIT_TYPES,
        form=form or {},
        error=error,
        is_admin=current_user()["role"] == "admin",
    ), status


@bp.get("/")
@login_required
def index():
    return _page()


@bp.post("/")
@admin_required
def create():
    form = request.form
    photo = None
    try:
        code = quick_items.validate_code(form.get("code"))
        try:
            group = GROUPS_BY_DIGIT[int(form.get("group"))]
        except (TypeError, ValueError, KeyError):
            raise QuickItemError("Choose which group it belongs to.")

        # The list groups items by the first digit of their code, so a code
        # from another group would put the item in the wrong place (a hair
        # pin with 111 would show under Dairy & drinks).
        coded = quick_items.group_for(code)
        if len(code) == 3 and coded["digit"] != group["digit"] and code[0] != "8" and code[0] != "0":
            suggestion = quick_items.next_free_code(group["digit"])
            raise QuickItemError(
                f"Code {code} is a {coded['label']} code. "
                f"{group['label']} codes start with {group['digit']}"
                + (f" - use {suggestion}." if suggestion else ".")
            )

        price = parse_money(form.get("price"), field="Price")
        cost_raw = (form.get("cost_price") or "").strip()
        cost = parse_money(cost_raw, field="What you pay", allow_zero=True) if cost_raw else 0
        try:
            starting_stock = int(form.get("stock") or 0)
        except ValueError:
            raise QuickItemError("How many you have now must be a number.")
        if starting_stock < 0:
            raise QuickItemError("How many you have now can't be negative.")

        photo = images.save_product_image(request.files.get("photo"))
        with transaction() as conn:
            product_id, warning = products.create(
                conn,
                barcode=code,
                name=form.get("name"),
                category=group["category"],
                unit_type=form.get("unit_type", "piece"),
                retail_price_cents=price,
                # No separate wholesale price: the same price at any quantity.
                wholesale_price_cents=price,
                wholesale_min_qty=6,
                cost_price_cents=cost,
                low_stock_threshold=5,
                track_expiry=form.get("track_expiry") == "on",
                created_by=current_user()["id"],
                image_path=photo,
            )
            if starting_stock and not form.get("track_expiry"):
                stock.record_stock_in(
                    conn, product_id=product_id, quantity=starting_stock,
                    cost_price_cents=cost, created_by=current_user()["id"],
                )
    except (QuickItemError, ProductError, MoneyError, ImageError, StockError) as err:
        images.delete_product_image(photo)
        return _page(form=form, error=str(err), status=400)

    if warning:
        flash(warning, "warning")
    name = " ".join((form.get("name") or "").split())
    flash(f"Saved! Type {code} at the till for {name}.", "success")
    # Straight back to the form, ready for the next one.
    return redirect(url_for("quick.index", group=group["digit"]) + "#create")


@bp.get("/print")
@login_required
def print_list():
    return render_template("quick_items_print.html", grouped=quick_items.grouped_items())


@bp.get("/api")
@login_required
def api_list():
    """The tiles on the till."""
    return jsonify([
        {key: item[key] for key in
         ("id", "code", "name", "price_display", "stock_quantity", "icon", "image_url")}
        | {"group": item["group"]["label"], "group_emoji": item["group"]["emoji"]}
        for item in quick_items.list_items()
    ])
