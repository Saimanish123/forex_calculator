"""Brokerage estimate from a platform's fee rule. Estimates only pre-fill forms — the figures on
the user's contract note always win."""

from dataclasses import dataclass
from decimal import Decimal

from ..money import HUNDRED, ZERO, q


@dataclass(frozen=True)
class FeeRule:
    brokerage_pct: Decimal = ZERO
    brokerage_min_usd: Decimal = ZERO
    brokerage_max_usd: Decimal | None = None
    brokerage_per_share_usd: Decimal = ZERO
    brokerage_flat_usd: Decimal = ZERO
    brokerage_gst_pct: Decimal = ZERO
    sell_fee_usd: Decimal = ZERO


@dataclass(frozen=True)
class TradeFees:
    brokerage_usd: Decimal
    brokerage_gst_usd: Decimal
    other_fees_usd: Decimal

    @property
    def total(self) -> Decimal:
        return self.brokerage_usd + self.brokerage_gst_usd + self.other_fees_usd


def estimate_fees(rule: FeeRule, side: str, quantity: Decimal, price: Decimal) -> TradeFees:
    """brokerage = clamp(gross × pct + qty × per-share + flat, min, max), rounded to the cent;
    GST on brokerage; `sell_fee_usd` on sells only."""
    if side not in ("buy", "sell"):
        raise ValueError("Fees apply to buys and sells only")
    gross = quantity * price
    brokerage = (
        gross * rule.brokerage_pct / HUNDRED
        + quantity * rule.brokerage_per_share_usd
        + rule.brokerage_flat_usd
    )
    if brokerage > 0 or rule.brokerage_min_usd > 0:
        brokerage = max(brokerage, rule.brokerage_min_usd)
    if rule.brokerage_max_usd is not None:
        brokerage = min(brokerage, rule.brokerage_max_usd)
    brokerage = q(brokerage)
    gst = q(brokerage * rule.brokerage_gst_pct / HUNDRED)
    other = q(rule.sell_fee_usd) if side == "sell" else ZERO
    return TradeFees(brokerage, gst, other)
