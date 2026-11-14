"""Exact money handling.

All authoritative amounts are integer paise. Decimal input is parsed with
Decimal (never float) and rejected when it carries more than two decimal
places, so no silent rounding can occur.
"""
import re
from decimal import Decimal

CURRENCY_SYMBOLS = {"INR": "\u20b9", "USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}

MAX_TOTAL_PAISE = 10**15
MAX_UNIT_PRICE_PAISE = 99_999_999_999
MAX_STOCK = 1_000_000_000
MAX_QUANTITY = 1_000_000
MAX_PRODUCT_ID = 9_223_372_036_854_775_807
MAX_TRANSACTION_LINES = 100

_AMOUNT_RE = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,2})?")


class MoneyError(ValueError):
    """Raised when a monetary input cannot be represented exactly in paise."""


def parse_paise(raw, label: str = "Amount", *, required: bool = True, allow_blank: bool = False):
    """Parse a user-supplied decimal string into integer paise.

    Accepts only ASCII digits, up to nine integral digits and at most two
    decimal places. Rejects whitespace, currency symbols and separators.
    """
    text = "" if raw is None else str(raw)
    if not text:
        if allow_blank:
            return None
        if required:
            raise MoneyError(f"{label} is required.")
        return 0

    if text.startswith("-"):
        raise MoneyError(f"{label} cannot be negative.")
    if not _AMOUNT_RE.fullmatch(text):
        raise MoneyError(
            f"{label} must have at most 9 integral digits and 2 decimal places, "
            "without spaces, separators or currency symbols."
        )

    value = Decimal(text)
    paise = int(value * 100)
    # Defensive: the regex already guarantees an exact 2dp representation.
    if Decimal(paise) / 100 != value:
        raise MoneyError(f"{label} has unsupported precision.")
    return paise


def parse_quantity(raw, label: str = "Quantity", *, minimum: int = 1, maximum: int = MAX_QUANTITY):
    """Whole-unit quantity parser. Stock is unit-based in this version."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValueError(f"{label} is required.")
    if not re.fullmatch(r"[0-9]{1,19}", text):
        raise ValueError(f"{label} must be a whole number.")
    value = int(text)
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    if value > maximum:
        raise ValueError(f"{label} is too large.")
    return value


def parse_product_id(raw) -> int:
    """Reject malformed or out-of-range SQLite IDs before binding a query."""
    return parse_quantity(raw, "Product ID", minimum=1, maximum=MAX_PRODUCT_ID)


def format_money(paise, currency: str = "INR") -> str:
    if paise is None:
        paise = 0
    negative = paise < 0
    paise = abs(int(paise))
    rupees, fraction = divmod(paise, 100)
    if currency == "INR":
        digits = str(rupees)
        if len(digits) > 3:
            head, tail = digits[:-3], digits[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            digits = ",".join(groups) + "," + tail
    else:
        digits = f"{rupees:,}"
    symbol = CURRENCY_SYMBOLS.get(currency, "")
    return f"{'-' if negative else ''}{symbol}{digits}.{fraction:02d}"


def money_input(paise) -> str:
    """Render paise as a plain decimal string suitable for form inputs."""
    if paise is None:
        return ""
    paise = int(paise)
    whole, fraction = divmod(abs(paise), 100)
    return f"{'-' if paise < 0 else ''}{whole}.{fraction:02d}"


def decimal_text_to_float(text: str) -> float:
    """Front-end preview only. Never used for stored financial values."""
    try:
        return int(Decimal(text) * 100) / 100
    except Exception:
        return 0.0
