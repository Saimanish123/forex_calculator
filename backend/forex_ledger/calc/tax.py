"""Capital gains tax estimate for selling foreign shares as an Indian resident.

Method (an estimate — confirm with a CA):
- Gain = sale value − transfer expenses − cost of acquisition (Section 48), computed in rupees.
- Each side is converted at SBI's TT buying rate on the last day of the month before that side's
  transaction (Rule 115 of the Income-tax Rules, 1962; Rule 206 under the Income-tax Act, 2025).
  If SBI published no rate that day, the latest rate within the previous 7 days is used.
- Held more than 24 months → long-term at 12.5% (no indexation, no ₹1.25 lakh exemption: that's
  for Indian listed equity only). Otherwise short-term at the user's slab rate.
- Within the sale, a short-term loss is set off against long-term gains; a long-term loss can't
  reduce short-term gains. 4% health & education cess is added; surcharge is not.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..config import IST
from ..money import HUNDRED, ZERO
from .portfolio import Lot

LTCG_RATE_PCT = Decimal("12.5")
CESS_PCT = Decimal("4")
LONG_TERM_MONTHS = 24


def month_end_before(moment: datetime) -> date:
    """Last day of the month before `moment`'s month (IST)."""
    return moment.astimezone(IST).date().replace(day=1) - timedelta(days=1)


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    for candidate in (day.day, 30, 29, 28):  # clamp e.g. 31 Jan + 1 month → 28/29 Feb
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def is_long_term(acquired: datetime, sold: datetime) -> bool:
    """Held for more than 24 months."""
    bought_on = acquired.astimezone(IST).date()
    return sold.astimezone(IST).date() > add_months(bought_on, LONG_TERM_MONTHS)


@dataclass(frozen=True)
class LotTax:
    lot: Lot
    long_term: bool
    sale_value_usd: Decimal  # this lot's share of sale value, net of sell fees
    cost_usd: Decimal
    sale_rate: Decimal | None
    cost_rate: Decimal | None
    gain_inr: Decimal | None


@dataclass(frozen=True)
class TaxEstimate:
    lots: tuple[LotTax, ...]
    short_term_inr: Decimal
    long_term_inr: Decimal
    taxable_short_inr: Decimal
    taxable_long_inr: Decimal
    tax_inr: Decimal
    slab_pct: Decimal
    complete: bool  # False when an SBI rate was missing for some lot


def estimate_capital_gains_tax(
    slices: tuple[Lot, ...],
    sold_at: datetime,
    gross: Decimal,
    sell_fees: Decimal,
    slab_pct: Decimal,
    tt_buying_rate: Callable[[date], Decimal | None],
) -> TaxEstimate:
    total_qty = sum((s.qty for s in slices), ZERO)
    sale_rate = tt_buying_rate(month_end_before(sold_at))
    lots, short, long, complete = [], ZERO, ZERO, True
    for s in slices:
        share = s.qty / total_qty if total_qty else ZERO
        sale_value = (gross - sell_fees) * share
        cost_rate = tt_buying_rate(month_end_before(s.acquired_at))
        long_term = is_long_term(s.acquired_at, sold_at)
        if sale_rate is None or cost_rate is None:
            gain, complete = None, False
        else:
            gain = sale_value * sale_rate - s.usd_cost * cost_rate
            if long_term:
                long += gain
            else:
                short += gain
        lots.append(LotTax(s, long_term, sale_value, s.usd_cost, sale_rate, cost_rate, gain))

    taxable_short, taxable_long = short, long
    if taxable_short < 0:
        taxable_long += taxable_short
        taxable_short = ZERO
    taxable_long = max(taxable_long, ZERO)
    base = taxable_short * slab_pct / HUNDRED + taxable_long * LTCG_RATE_PCT / HUNDRED
    tax = base * (HUNDRED + CESS_PCT) / HUNDRED
    return TaxEstimate(
        tuple(lots), short, long, taxable_short, taxable_long, tax, slab_pct, complete
    )
