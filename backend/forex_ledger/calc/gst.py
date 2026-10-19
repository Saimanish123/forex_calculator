"""GST on currency conversion — default slab (SPEC §6.4). Estimates only; entered actuals win."""

from decimal import Decimal

LAKH = Decimal("100000")
TEN_LAKH = Decimal("1000000")


def conversion_gst(value_inr: Decimal) -> Decimal:
    if value_inr <= 0:
        return Decimal("0")
    if value_inr <= LAKH:
        return max(value_inr * Decimal("0.0018"), Decimal("45"))
    if value_inr <= TEN_LAKH:
        return Decimal("180") + (value_inr - LAKH) * Decimal("0.0009")
    return min(Decimal("990") + (value_inr - TEN_LAKH) * Decimal("0.00018"), Decimal("10800"))
