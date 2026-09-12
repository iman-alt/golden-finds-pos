"""
Money handling.

Every monetary value in this system is stored and passed around as an
integer number of cents. Floats are never used for money - 0.1 + 0.2 is
not 0.3, and a till that is off by a cent a hundred times a day is a till
nobody trusts.

Conversion to and from the decimal shillings a human types happens only
at the edges: `parse_money` on the way in, `format_money` on the way out.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCY_SYMBOL = "KSh"


class MoneyError(ValueError):
    """Raised when a user-supplied amount cannot be read as money."""


def parse_money(value, *, field="amount", allow_zero=False):
    """
    Turns user input ("1,250.50", "48", 48.0, Decimal("48")) into cents.

    Uses Decimal rather than float so that "0.07" means exactly 7 cents.
    Rounds half-up, the way a person expects, not half-to-even.

    Raises MoneyError with a message safe to show the user.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise MoneyError(f"{field} is required.")

    if isinstance(value, str):
        value = value.strip().replace(",", "").replace(CURRENCY_SYMBOL, "").strip()

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MoneyError(f"{field} must be a number.")

    if not amount.is_finite():
        raise MoneyError(f"{field} must be a number.")
    if amount < 0:
        raise MoneyError(f"{field} cannot be negative.")
    if amount == 0 and not allow_zero:
        raise MoneyError(f"{field} must be greater than zero.")

    cents = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def format_money(cents, *, symbol=True):
    """
    Renders cents for display. Whole shillings drop the ".00" because
    that is how prices are written on a shelf in Nairobi; anything with
    cents keeps both decimal places.
    """
    if cents is None:
        return ""
    shillings = Decimal(cents) / 100
    if shillings == shillings.to_integral_value():
        text = f"{int(shillings):,}"
    else:
        text = f"{shillings:,.2f}"
    return f"{CURRENCY_SYMBOL} {text}" if symbol else text
