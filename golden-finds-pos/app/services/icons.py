"""
Product icons.

A small picture next to each cart line makes it scannable at a glance -
useful when someone is reading a cart back to a customer with a queue
waiting. Icons are derived, never stored: a product that gets renamed
picks up the right one on its own, and nobody has to tag anything.

Keyword matching is deliberately dumb and deliberately Kenyan - these are
the words that actually appear on a shelf in this shop, not a generic
supermarket taxonomy.
"""

# Checked in order, first match wins, so put the specific before the
# general: "milk" must be tested before "mil" would ever match anything,
# and "sugar" before a broader grocery fallback.
KEYWORD_ICONS = (
    # Staples
    (("unga", "maize flour", "flour", "ugali"), "🌽"),
    (("rice", "pishori", "basmati"), "🍚"),
    (("sugar", "sukari"), "🍬"),
    (("salt", "chumvi"), "🧂"),
    (("cooking oil", "oil", "mafuta", "elianto", "fresh fri"), "🫒"),
    (("beans", "njahi", "ndengu", "lentil", "green gram"), "🫘"),
    (("spaghetti", "pasta", "macaroni", "noodle"), "🍝"),
    # Dairy and eggs
    (("milk", "maziwa", "lala", "yoghurt", "yogurt", "cream"), "🥛"),
    (("butter", "blueband", "blue band", "margarine", "ghee"), "🧈"),
    (("cheese",), "🧀"),
    (("egg", "mayai"), "🥚"),
    # Bakery
    (("bread", "mkate", "loaf", "bun", "scone"), "🍞"),
    (("cake", "queen cake"), "🍰"),
    (("biscuit", "cookie", "digestive", "marie"), "🍪"),
    # Drinks
    (("water", "maji", "dasani", "keringet"), "💧"),
    (("soda", "coke", "coca", "cola", "fanta", "sprite", "pepsi",
      "stoney", "krest", "novida"), "🥤"),
    (("juice", "afia", "delmonte", "minute maid"), "🧃"),
    (("tea", "chai", "ketepa"), "🍵"),
    (("coffee", "dormans", "nescafe"), "☕"),
    (("beer", "wine", "spirit", "whisky", "vodka"), "🍾"),
    # Fresh
    (("tomato", "nyanya"), "🍅"),
    (("onion", "kitunguu"), "🧅"),
    (("potato", "viazi", "waru"), "🥔"),
    (("banana", "ndizi"), "🍌"),
    (("mango", "embe"), "🥭"),
    (("avocado", "parachichi"), "🥑"),
    (("orange", "chungwa", "lemon", "ndimu"), "🍊"),
    (("cabbage", "sukuma", "kale", "spinach", "managu"), "🥬"),
    (("carrot", "karoti"), "🥕"),
    (("apple", "tufaha"), "🍎"),
    (("meat", "nyama", "beef", "goat", "mutton"), "🥩"),
    (("chicken", "kuku"), "🍗"),
    (("fish", "samaki", "omena", "tilapia"), "🐟"),
    # Household
    (("omo", "ariel", "sunlight", "detergent", "washing", "persil"), "🧺"),
    (("soap", "sabuni", "geisha", "imperial leather", "dettol"), "🧼"),
    (("toilet", "tissue", "hanan", "roll"), "🧻"),
    (("jik", "bleach", "harpic", "toss"), "🧴"),
    (("matches", "kiberiti", "candle", "mshumaa"), "🕯️"),
    (("charcoal", "makaa", "paraffin", "gas", "cylinder"), "🔥"),
    (("broom", "mop", "brush", "sponge"), "🧹"),
    (("battery", "bulb", "torch"), "🔋"),
    # Personal care
    (("toothpaste", "colgate", "toothbrush", "mswaki"), "🪥"),
    (("lotion", "vaseline", "nivea", "jelly", "cream"), "🧴"),
    (("pad", "always", "sanitary", "tampon"), "🩹"),
    (("diaper", "pamper", "huggies", "nappy"), "🍼"),
    (("shampoo", "hair", "relaxer", "weave"), "💇"),
    (("perfume", "deodorant", "roll on", "spray"), "💨"),
    (("razor", "shaving", "blade"), "🪒"),
    # Baby
    (("baby", "infant", "nan ", "cerelac"), "🍼"),
    # Other
    (("panadol", "medicine", "tablet", "syrup", "dawa"), "💊"),
    (("pen", "book", "exercise", "ruler", "pencil"), "📒"),
    (("airtime", "scratch card", "safaricom"), "📱"),
)

CATEGORY_ICONS = {
    "Groceries & Food Items": "🛍️",
    "Personal Care & Cosmetics": "🧴",
    "Household Essentials": "🧽",
    "Baby Care Products": "🍼",
    "Fashion & Accessories": "👗",
    "Other": "📦",
}

DEFAULT_ICON = "📦"


def icon_for(name, category=None):
    """
    Picks an icon from the product's name, falling back to its category
    and finally to a plain box. Never raises and never returns empty -
    a missing icon would leave a ragged column in the cart.
    """
    haystack = (name or "").lower()

    for keywords, icon in KEYWORD_ICONS:
        if any(keyword in haystack for keyword in keywords):
            return icon

    return CATEGORY_ICONS.get(category, DEFAULT_ICON)
