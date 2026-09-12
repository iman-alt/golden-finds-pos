"""
Product photos: what gets accepted, what gets stored, and what gets removed.
"""

import io
from pathlib import Path

from PIL import Image

from app.db import query_one


def _photo(fmt="PNG", size=(1200, 700), colour=(233, 169, 43), exif=None):
    buffer = io.BytesIO()
    image = Image.new("RGB", size, colour)
    if exif is not None:
        image.save(buffer, fmt, exif=exif)
    else:
        image.save(buffer, fmt)
    buffer.seek(0)
    return buffer


def _login_owner(client):
    client.post("/login", data={"name": "Owner", "pin": "482913"})


def _new_product_form(photo=None, filename="pack.png", barcode="616100000001"):
    data = {
        "barcode": barcode,
        "name": "Jik 750ml",
        "category": "Household Essentials",
        "unit_type": "piece",
        "cost_price": "150",
        "retail_price": "190",
        "wholesale_price": "180",
        "then": "sell",
    }
    if photo is not None:
        data["photo"] = (photo, filename)
    return data


def _stored_file(app, image_path):
    return Path(app.config["UPLOAD_DIR"]) / image_path.split("/", 1)[1]


def test_a_product_can_be_saved_with_a_photo(app, client, owner):
    _login_owner(client)
    response = client.post("/add-product", data=_new_product_form(_photo()),
                           content_type="multipart/form-data")
    assert response.status_code == 302

    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '616100000001'")
    assert product["image_path"].startswith("product_images/")
    assert _stored_file(app, product["image_path"]).exists()


def test_photos_are_cropped_square_and_shrunk(app, client, owner):
    _login_owner(client)
    client.post("/add-product", data=_new_product_form(_photo(size=(4000, 3000))),
                content_type="multipart/form-data")

    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '616100000001'")
    stored = Image.open(_stored_file(app, product["image_path"]))
    assert stored.size == (800, 800)
    assert stored.format == "JPEG"


def test_location_and_camera_data_are_removed(app, client, owner):
    """Phone photos can carry where they were taken. None of it is kept."""
    exif = Image.Exif()
    exif[0x0110] = "Phone Model X"      # camera model
    exif[0x8825] = {1: "S", 2: (1.0, 17.0, 0.0)}  # GPS info
    _login_owner(client)
    client.post("/add-product",
                data=_new_product_form(_photo("JPEG", exif=exif), "pack.jpg"),
                content_type="multipart/form-data")

    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '616100000001'")
    stored = Image.open(_stored_file(app, product["image_path"]))
    assert len(stored.getexif()) == 0


def test_a_file_that_is_not_an_image_is_refused(app, client, owner):
    """Renaming a file to .jpg must not get it onto the server."""
    _login_owner(client)
    fake = io.BytesIO(b"<?php echo 'not a photo'; ?>")
    response = client.post("/add-product",
                           data=_new_product_form(fake, "totally-a-photo.jpg"),
                           content_type="multipart/form-data")

    assert response.status_code == 400
    assert "isn't a photo" in response.get_data(as_text=True)
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM products")["n"] == 0
    assert list(Path(app.config["UPLOAD_DIR"]).iterdir()) == []


def test_a_photo_is_optional(app, client, owner):
    _login_owner(client)
    response = client.post("/add-product", data=_new_product_form(),
                           content_type="multipart/form-data")
    assert response.status_code == 302
    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '616100000001'")
    assert product["image_path"] is None


def test_a_failed_product_save_leaves_no_orphan_photo(app, client, owner, make_product):
    make_product(barcode="616100000001")  # the barcode is already taken
    _login_owner(client)
    response = client.post("/add-product", data=_new_product_form(_photo()),
                           content_type="multipart/form-data")

    assert response.status_code == 400
    assert list(Path(app.config["UPLOAD_DIR"]).iterdir()) == []


def test_replacing_a_photo_deletes_the_old_file(app, client, owner):
    _login_owner(client)
    client.post("/add-product", data=_new_product_form(_photo()),
                content_type="multipart/form-data")
    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '616100000001'")
    old_file = _stored_file(app, product["image_path"])

    client.post(f"/products/{product['id']}/edit", data={
        "name": "Jik 750ml", "category": "Household Essentials", "unit_type": "piece",
        "cost_price": "150", "retail_price": "190", "wholesale_price": "180",
        "wholesale_min_qty": "6", "low_stock_threshold": "5", "active": "on",
        "photo": (_photo(colour=(79, 143, 115)), "new.png"),
    }, content_type="multipart/form-data")

    with app.app_context():
        updated = query_one("SELECT * FROM products WHERE id = ?", (product["id"],))
    assert updated["image_path"] != product["image_path"]
    assert _stored_file(app, updated["image_path"]).exists()
    assert not old_file.exists(), "the replaced photo was cleaned up"


def test_a_photo_can_be_removed(app, client, owner):
    _login_owner(client)
    client.post("/add-product", data=_new_product_form(_photo()),
                content_type="multipart/form-data")
    with app.app_context():
        product = query_one("SELECT * FROM products WHERE barcode = '616100000001'")

    client.post(f"/products/{product['id']}/edit", data={
        "name": "Jik 750ml", "category": "Household Essentials", "unit_type": "piece",
        "cost_price": "150", "retail_price": "190", "wholesale_price": "180",
        "wholesale_min_qty": "6", "low_stock_threshold": "5", "active": "on",
        "remove_photo": "on",
    }, content_type="multipart/form-data")

    with app.app_context():
        updated = query_one("SELECT * FROM products WHERE id = ?", (product["id"],))
    assert not updated["image_path"]
    assert not _stored_file(app, product["image_path"]).exists()


def test_the_till_is_given_the_photo(app, client, owner):
    _login_owner(client)
    client.post("/add-product", data=_new_product_form(_photo()),
                content_type="multipart/form-data")

    data = client.get("/api/product/616100000001").get_json()
    assert data["image_url"].startswith("/static/product_images/")

    priced = client.post("/api/cart/price", json={
        "items": [{"product_id": data["id"], "quantity": 1}],
    }).get_json()
    assert priced["lines"][0]["image_url"] == data["image_url"]


def test_a_shopkeeper_cannot_upload_photos(app, client, owner, cashier):
    client.post("/login", data={"name": "Amina", "pin": "573820"})
    response = client.post("/add-product", data=_new_product_form(_photo()),
                           content_type="multipart/form-data")
    assert response.status_code in (302, 403)
    assert list(Path(app.config["UPLOAD_DIR"]).iterdir()) == []


def test_deleting_refuses_paths_outside_the_photo_folder(app, tmp_path):
    from app.services.images import delete_product_image

    outsider = tmp_path / "keep-me.txt"
    outsider.write_text("important")
    with app.test_request_context():
        delete_product_image("product_images/../../" + str(outsider))
        delete_product_image(str(outsider))
    assert outsider.exists()
