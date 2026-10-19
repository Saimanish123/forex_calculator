"""Decimal helpers. Money and rates are never floats."""

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
# Rates are stored and shown with 4 decimals, so the rate on screen is the rate used in the math.
RATE_PLACES = 4


def D(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value))


def q(value: Decimal | None, places: int = 2) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def fmt(value: Decimal | None, places: int = 2) -> str | None:
    """Rounded decimal as a string for JSON (FastAPI would otherwise turn Decimals into floats)."""
    return None if value is None else str(q(value, places))
