"""
Product photos.

Mum photographs the actual pack on her phone when she adds a product, and
that photo is what shows at the till. That keeps it offline and accurate,
and avoids using brand images from the internet that the shop has no
licence for.

Every upload is opened and re-encoded rather than stored as sent:

  * Anything that is not really a JPEG, PNG or WebP is refused, whatever
    its filename claims. A renamed file cannot get onto the server.
  * Phone photos carry EXIF data, often including where they were taken.
    Re-encoding drops all of it.
  * A 4 MB, 4000-pixel phone photo is cropped to a centred square and
    shrunk, so the till loads it instantly and the disk does not fill up.
"""

import secrets
from pathlib import Path

from flask import current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

PHOTO_SIZE = 800
URL_PREFIX = "product_images"
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}  # MPO: some phone cameras

# Refuse images big enough to be a decompression bomb, long before they
# can use up the till's memory. 40 megapixels is well past any phone.
Image.MAX_IMAGE_PIXELS = 40_000_000


class ImageError(ValueError):
    """Raised when an upload is not a usable photo. Message is user-safe."""


def _upload_dir():
    folder = Path(current_app.config["UPLOAD_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_product_image(file_storage):
    """
    Validates, cleans and stores an uploaded photo.

    Returns the stored path (for products.image_path), or None when no file
    was chosen - leaving the photo out is always allowed.
    """
    if file_storage is None or not file_storage.filename:
        return None

    try:
        probe = Image.open(file_storage.stream)
        probe.verify()  # catches truncated and corrupted files
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image_format = image.format
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError,
            Image.DecompressionBombError):
        raise ImageError("That file isn't a photo we can use. Try a JPEG or PNG.")

    if image_format not in ACCEPTED_FORMATS:
        raise ImageError("Photos must be JPEG, PNG or WebP.")

    # Phones store "rotate me" as a tag rather than rotating the pixels.
    # Apply it now, because re-encoding is about to drop the tag.
    image = ImageOps.exif_transpose(image)

    if image.mode in ("RGBA", "LA", "P"):
        # Transparent PNGs get a white background rather than black.
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    side = min(PHOTO_SIZE, *image.size)
    image = ImageOps.fit(image, (side, side), Image.LANCZOS)

    filename = f"{secrets.token_hex(12)}.jpg"
    # No exif argument: nothing from the original file is written back.
    image.save(_upload_dir() / filename, "JPEG", quality=84, optimize=True)
    return f"{URL_PREFIX}/{filename}"


def delete_product_image(stored_path):
    """
    Removes a stored photo file. Quietly does nothing for an empty path, a
    missing file, or a path that points anywhere outside the photo folder.
    """
    if not stored_path or not stored_path.startswith(f"{URL_PREFIX}/"):
        return

    folder = _upload_dir().resolve()
    target = (folder / stored_path.split("/", 1)[1]).resolve()
    if target.parent != folder:
        return
    try:
        target.unlink()
    except FileNotFoundError:
        pass


def image_url(stored_path):
    """The URL a page uses to show the photo, or None when there isn't one."""
    if not stored_path:
        return None
    return url_for("static", filename=stored_path)
