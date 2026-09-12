"""
Products and stock receiving.
"""

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for
)

from ..db import transaction
from ..money import MoneyError, parse_money
from ..security import admin_required, current_user, login_required
from ..services import products, stock
from ..services import images
from ..services.images import ImageError
from ..services.products import CATEGORIES, UNIT_TYPES, ProductError

bp = Blueprint("inventory", __name__)


@bp.get("/products")
@login_required
def product_list():
    return render_template(
        "products.html",
        products=products.list_products(
            search_term=request.args.get("q") or None,
            category=request.args.get("category") or None,
            include_inactive=request.args.get("all") == "1",
        ),
        categories=CATEGORIES,
        filters=request.args,
        is_admin=current_user()["role"] == "admin",
    )


@bp.route("/add-product", methods=["GET", "POST"])
@admin_required
def add_product():
    """
    Reached automatically when an unknown barcode is scanned, with the
    barcode prefilled - the cashier should never have to retype it.
    """
    if request.method == "GET":
        return render_template(
            "add_product.html",
            barcode=request.args.get("barcode", ""),
            categories=CATEGORIES,
            unit_types=UNIT_TYPES,
            form={},
        )

    form = request.form
    try:
        product_id, warning = _save_new_product(form)
    except (ProductError, MoneyError, ImageError) as err:
        return render_template(
            "add_product.html",
            barcode=form.get("barcode", ""),
            categories=CATEGORIES,
            unit_types=UNIT_TYPES,
            error=str(err),
            form=form,
        ), 400

    if warning:
        flash(warning, "warning")
    flash(f"{form.get('name')} added.", "success")

    # Straight to stock-in if they said they have stock to receive,
    # otherwise back to the till to finish serving the customer.
    if form.get("then") == "stock_in":
        return redirect(url_for("inventory.stock_in_screen",
                                barcode=form.get("barcode")))
    return redirect(url_for("sell.screen", added=form.get("barcode")))


def _save_new_product(form):
    # The photo is checked and stored first, so a bad file is reported
    # before anything is saved - and removed again if the product itself
    # then fails to save, so no orphan files pile up.
    photo = images.save_product_image(request.files.get("photo"))
    try:
        return _create_product(form, photo)
    except Exception:
        images.delete_product_image(photo)
        raise


def _create_product(form, photo):
    with transaction() as conn:
        return products.create(
            conn,
            image_path=photo,
            barcode=form.get("barcode", ""),
            name=form.get("name", ""),
            category=form.get("category", ""),
            unit_type=form.get("unit_type", "piece"),
            retail_price_cents=parse_money(
                form.get("retail_price"), field="Retail price"),
            wholesale_price_cents=parse_money(
                form.get("wholesale_price"), field="Wholesale price"),
            cost_price_cents=parse_money(
                form.get("cost_price"), field="Cost price", allow_zero=True),
            wholesale_min_qty=int(form.get("wholesale_min_qty") or 6),
            low_stock_threshold=int(form.get("low_stock_threshold") or 5),
            track_expiry=form.get("track_expiry") == "on",
            created_by=current_user()["id"],
        )


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    product = products.get(product_id)
    if product is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "edit_product.html", product=product,
            current_image=images.image_url(product["image_path"]),
            categories=CATEGORIES, unit_types=UNIT_TYPES,
            movements=stock.get_movements(product_id, limit=50),
        )

    form = request.form
    new_photo = None
    remove_photo = form.get("remove_photo") == "on"
    try:
        new_photo = images.save_product_image(request.files.get("photo"))
        with transaction() as conn:
            warning = products.update(
                conn, product_id,
                updated_by=current_user()["id"],
                # "" clears the photo; None leaves it as it was.
                image_path=new_photo or ("" if remove_photo else None),
                name=form.get("name"),
                category=form.get("category"),
                unit_type=form.get("unit_type"),
                retail_price_cents=parse_money(
                    form.get("retail_price"), field="Retail price"),
                wholesale_price_cents=parse_money(
                    form.get("wholesale_price"), field="Wholesale price"),
                cost_price_cents=parse_money(
                    form.get("cost_price"), field="Cost price", allow_zero=True),
                wholesale_min_qty=int(form.get("wholesale_min_qty") or 6),
                low_stock_threshold=int(form.get("low_stock_threshold") or 5),
                track_expiry=1 if form.get("track_expiry") == "on" else 0,
                active=1 if form.get("active") == "on" else 0,
            )
    except (ProductError, MoneyError, ImageError) as err:
        images.delete_product_image(new_photo)
        flash(str(err), "error")
        return redirect(url_for("inventory.edit_product", product_id=product_id))

    # The old file goes only once the change is safely recorded.
    if (new_photo or remove_photo) and product["image_path"]:
        images.delete_product_image(product["image_path"])

    if warning:
        flash(warning, "warning")
    flash("Product updated.", "success")
    return redirect(url_for("inventory.product_list"))


@bp.get("/stock-in")
@admin_required
def stock_in_screen():
    return render_template("stock_in.html", barcode=request.args.get("barcode", ""))


@bp.route("/products/<int:product_id>/adjust", methods=["GET", "POST"])
@admin_required
def adjust_stock(product_id):
    """
    Stock takes and write-offs. Owner-only, because a cashier who can
    silently set stock to whatever they like can cover a shortfall.
    """
    product = products.get(product_id)
    if product is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "adjust_stock.html", product=product,
            movements=stock.get_movements(product_id, limit=30),
        )

    try:
        with transaction() as conn:
            difference = stock.adjust(
                conn,
                product_id=product_id,
                new_quantity=request.form.get("new_quantity", type=int),
                reason=request.form.get("reason", "count_adjustment"),
                created_by=current_user()["id"],
            )
    except stock.StockError as err:
        flash(str(err), "error")
        return redirect(url_for("inventory.adjust_stock", product_id=product_id))

    if difference == 0:
        flash("Stock was already at that figure - nothing changed.", "info")
    else:
        flash(f"Stock adjusted by {difference:+d}.", "success")
    return redirect(url_for("inventory.product_list"))
