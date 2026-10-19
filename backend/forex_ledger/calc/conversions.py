"""Per-conversion analysis: what you got vs. the market and vs. a bank (SPEC §6.1).

Amounts are what moved in the user's bank / wallet:
- deposit:    inr_amount = ₹ debited (incl. charges and TCS), usd_amount = $ credited
- withdrawal: inr_amount = ₹ received (after charges),       usd_amount = $ withdrawn
"""

from dataclasses import dataclass
from decimal import Decimal

from ..money import HUNDRED, ZERO


@dataclass(frozen=True)
class ConversionAnalysis:
    converted_inr: Decimal  # ₹ actually exchanged, excluding charges and TCS
    rate_got: Decimal  # ₹ per $
    market_inr: Decimal  # $ amount valued at mid-market
    markup_inr: Decimal  # lost to the exchange rate (positive = cost)
    markup_pct: Decimal  # as a percentage of mid-market
    charges_inr: Decimal
    hidden_cost_inr: Decimal  # markup + charges
    bank_inr: Decimal | None  # what the bank's rate would have meant for the same $
    vs_bank_inr: Decimal | None  # positive = the platform beat the bank


def analyze_conversion(
    kind: str,
    inr_amount: Decimal,
    usd_amount: Decimal,
    market_rate: Decimal,
    charges_inr: Decimal = ZERO,
    tcs_inr: Decimal = ZERO,
    bank_rate: Decimal | None = None,
) -> ConversionAnalysis:
    if usd_amount <= 0 or inr_amount <= 0 or market_rate <= 0:
        raise ValueError("Amounts and rates must be positive")

    market_inr = usd_amount * market_rate
    bank_inr = usd_amount * bank_rate if bank_rate else None

    if kind == "deposit":
        converted = inr_amount - charges_inr - tcs_inr
        if converted <= 0:
            raise ValueError("Charges and TCS can't exceed the amount debited")
        markup = converted - market_inr
        vs_bank = bank_inr - converted if bank_inr is not None else None
    elif kind == "withdrawal":
        converted = inr_amount + charges_inr
        markup = market_inr - converted
        vs_bank = converted - bank_inr if bank_inr is not None else None
    else:
        raise ValueError(f"Unknown conversion kind: {kind}")

    return ConversionAnalysis(
        converted_inr=converted,
        rate_got=converted / usd_amount,
        market_inr=market_inr,
        markup_inr=markup,
        markup_pct=markup / market_inr * HUNDRED,
        charges_inr=charges_inr,
        hidden_cost_inr=markup + charges_inr,
        bank_inr=bank_inr,
        vs_bank_inr=vs_bank,
    )
