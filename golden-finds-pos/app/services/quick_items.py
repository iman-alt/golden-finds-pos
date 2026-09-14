"""
Quick items - things with no barcode, like hair pins, loose sugar or a
single sweet.

Each gets a short number code of up to 6 digits (111 for milk, 222 for
hair pins). Typing the code at the till and pressing Enter works exactly
like scanning a barcode, and the till also shows them as one-tap tiles.

A quick item is just a product whose barcode is a short number, so it
takes part in everything else unchanged: stock, prices, offers, expiry,
reports and deni. Real barcodes are 8 digits or more, so the two never
clash.
"""

import re

from ..db import query_all
from ..money import format_money
from .icons import icon_for
from .images import image_url

MAX_CODE_LENGTH = 6

# Codes are grouped by their first digit so they are easy to guess:
# 1xx is dairy and drinks, 2xx hair and beauty, and so on.
GROUPS = (
    {"digit": 1, "label": "Dairy & drinks", "emoji": "🥛", "category": "Groceries & Food Items"},
    {"digit": 2, "label": "Hair & beauty", "emoji": "💇", "category": "Personal Care & Cosmetics"},
    {"digit": 3, "label": "Loose foods", "emoji": "🌾", "category": "Groceries & Food Items"},
    {"digit": 4, "label": "Household", "emoji": "🧽", "category": "Household Essentials"},
    {"digit": 5, "label": "Baby", "emoji": "🍼", "category": "Baby Care Products"},
    {"digit": 6, "label": "Fashion", "emoji": "👗", "category": "Fashion & Accessories"},
    {"digit": 7, "label": "Sweets & snacks", "emoji": "🍬", "category": "Groceries & Food Items"},
    {"digit": 9, "label": "Other", "emoji": "📦", "category": "Other"},
)
GROUPS_BY_DIGIT = {group["digit"]: group for group in GROUPS}

_SHORT_CODE = re.compile(rf"\d{{1,{MAX_CODE_LENGTH}}}")


class QuickItemError(ValueError):
    """Raised when a quick-item code can't be used. Message is user-safe."""


def is_quick_code(code):
    return bool(_SHORT_CODE.fullmatch((code or "").strip()))


def validate_code(code):
    code = (code or "").strip()
    if not code:
        raise QuickItemError("Give it a number code, like 111.")
    if not is_quick_code(code):
        raise QuickItemError(
            f"Use numbers only, up to {MAX_CODE_LENGTH} digits - like 111 or 2201."
        )
    return code


def group_for(code):
    """The group a code belongs to, by its first digit."""
    if len(code) == 3 and int(code[0]) in GROUPS_BY_DIGIT:
        return GROUPS_BY_DIGIT[int(code[0])]
    return GROUPS_BY_DIGIT[9]


def _used_codes():
    return {
        row["barcode"] for row in query_all(
            f"SELECT barcode FROM products WHERE length(barcode) <= {MAX_CODE_LENGTH} "
            "AND barcode NOT GLOB '*[^0-9]*'"
        )
    }


def next_free_code(digit):
    """
    The first unused 3-digit code in a group, counting 111, 112, ... so the
    codes look tidy, then 101-110 and 120-199. None if the group is full.
    """
    used = _used_codes()
    base = digit * 100
    order = list(range(base + 11, base + 100)) + list(range(base + 1, base + 11))
    for number in order:
        if str(number) not in used:
            return str(number)
    return None


def suggestions():
    """The next free code for every group, for the create form."""
    return {group["digit"]: next_free_code(group["digit"]) for group in GROUPS}


def list_items(include_inactive=False):
    clause = "" if include_inactive else "AND active = 1"
    rows = query_all(
        f"""
        SELECT * FROM products
        WHERE length(barcode) <= {MAX_CODE_LENGTH}
          AND barcode NOT GLOB '*[^0-9]*'
          {clause}
        ORDER BY length(barcode), CAST(barcode AS INTEGER)
        """
    )
    return [to_card(row) for row in rows]


def grouped_items():
    """Quick items bundled under their groups, in group order, empty groups dropped."""
    buckets = {group["digit"]: [] for group in GROUPS}
    for item in list_items():
        buckets[item["group"]["digit"]].append(item)
    return [
        {**group, "items": buckets[group["digit"]]}
        for group in GROUPS if buckets[group["digit"]]
    ]


def to_card(product):
    code = product["barcode"]
    return {
        "id": product["id"],
        "code": code,
        "name": product["name"],
        "category": product["category"],
        "price_cents": product["retail_price_cents"],
        "price_display": format_money(product["retail_price_cents"]),
        "stock_quantity": product["stock_quantity"],
        "icon": icon_for(product["name"], product["category"]),
        "image_url": image_url(product["image_path"]),
        "group": group_for(code),
        "active": bool(product["active"]),
    }
