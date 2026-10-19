"""Hypothetical exits: sell N shares now, or sell everything and convert it all now (SPEC §6.3).

Every rupee result is split so the parts add up exactly:
    net = stock gain − brokerage + currency movement − entry costs − exit markup − conversion GST
Nothing here mutates the ledger; lots are copied before being consumed.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from ..money import HUNDRED, ZERO
from .fees import FeeRule, TradeFees, estimate_fees
from .gst import conversion_gst
from .portfolio import Lot, PlatformState


@dataclass(frozen=True)
class ExitBreakdown:
    """Selling shares (and/or converting dollars) and bringing the money home at rate `m`."""

    sale_gross: Decimal  # S, $ (0 for cash only)
    sell_fees: Decimal  # $
    cash_usd: Decimal  # uninvested dollars converted alongside (portfolio exit)
    cost_usd: Decimal  # $ cost of the shares sold (buy price + buy fees)
    buy_gross: Decimal
    buy_fees: Decimal
    inr_cost: Decimal  # all-in ₹ cost of shares sold + cash converted
    mkt_cost: Decimal  # their market basis
    rate: Decimal  # mid-market ₹/$
    exit_markup_pct: Decimal
    conversion_gst_inr: Decimal

    @property
    def usd_out(self) -> Decimal:
        """Dollars converted: sale proceeds plus any cash."""
        return self.sale_gross - self.sell_fees + self.cash_usd

    @property
    def market_inr(self) -> Decimal:
        return self.usd_out * self.rate

    @property
    def exit_markup_inr(self) -> Decimal:
        return self.market_inr * self.exit_markup_pct / HUNDRED

    @property
    def inr_received(self) -> Decimal:
        return self.market_inr - self.exit_markup_inr - self.conversion_gst_inr

    @property
    def net_inr(self) -> Decimal:
        return self.inr_received - self.inr_cost

    # --- the parts of net_inr ---
    @property
    def stock_gain_inr(self) -> Decimal:
        return (self.sale_gross - self.buy_gross) * self.rate

    @property
    def brokerage_inr(self) -> Decimal:
        return (self.sell_fees + self.buy_fees) * self.rate

    @property
    def currency_inr(self) -> Decimal:
        return (self.cost_usd + self.cash_usd) * self.rate - self.mkt_cost

    @property
    def entry_costs_inr(self) -> Decimal:
        return self.inr_cost - self.mkt_cost

    @property
    def keep_in_usd(self) -> Decimal:
        """Profit in dollars if the proceeds stay in the wallet (shares only)."""
        return self.sale_gross - self.sell_fees - self.cost_usd


def take_fifo(lots: list[Lot], qty: Decimal) -> tuple[Lot, ...]:
    held = sum((lot.qty for lot in lots), ZERO)
    if qty <= 0 or qty > held:
        raise ValueError(f"You hold {held.normalize():f} shares; enter a quantity up to that")
    working, slices, remaining = copy.deepcopy(lots), [], qty
    while remaining > 0:
        part = working[0].split_off(remaining)
        slices.append(part)
        remaining -= part.qty
        if working[0].qty == 0:
            working.pop(0)
    return tuple(slices)


def sell_shares(
    slices: tuple[Lot, ...],
    quantity: Decimal,
    price: Decimal,
    fees: TradeFees,
    rate: Decimal,
    exit_markup_pct: Decimal,
    with_gst: bool,
) -> ExitBreakdown:
    gross = quantity * price
    usd_out = gross - fees.total
    gst = conversion_gst(usd_out * rate) if with_gst and usd_out > 0 else ZERO
    return ExitBreakdown(
        sale_gross=gross,
        sell_fees=fees.total,
        cash_usd=ZERO,
        cost_usd=sum((s.usd_cost for s in slices), ZERO),
        buy_gross=sum((s.gross for s in slices), ZERO),
        buy_fees=sum((s.fees for s in slices), ZERO),
        inr_cost=sum((s.inr for s in slices), ZERO),
        mkt_cost=sum((s.mkt for s in slices), ZERO),
        rate=rate,
        exit_markup_pct=exit_markup_pct,
        conversion_gst_inr=gst,
    )


def sell_everything(
    state: PlatformState,
    prices: dict[str, Decimal],
    rule: FeeRule,
    rate: Decimal,
    exit_markup_pct: Decimal,
    with_gst: bool,
) -> ExitBreakdown:
    """Sell every holding at `prices` (fees from the platform rule) and convert all dollars."""
    gross = sell_fees = buy_gross = buy_fees = inr = mkt = ZERO
    for ticker, lots in state.lots.items():
        qty = sum((lot.qty for lot in lots), ZERO)
        if qty == 0:
            continue
        gross += qty * prices[ticker]
        sell_fees += estimate_fees(rule, "sell", qty, prices[ticker]).total
        for lot in lots:
            buy_gross += lot.gross
            buy_fees += lot.fees
            inr += lot.inr
            mkt += lot.mkt
    cash = state.pool.usd
    usd_out = gross - sell_fees + cash
    gst = conversion_gst(usd_out * rate) if with_gst and usd_out > 0 else ZERO
    return ExitBreakdown(
        sale_gross=gross,
        sell_fees=sell_fees,
        cash_usd=cash,
        cost_usd=buy_gross + buy_fees,
        buy_gross=buy_gross,
        buy_fees=buy_fees,
        inr_cost=inr + state.pool.inr,
        mkt_cost=mkt + state.pool.mkt,
        rate=rate,
        exit_markup_pct=exit_markup_pct,
        conversion_gst_inr=gst,
    )


def solve_increasing(f: Callable[[Decimal], Decimal], target: Decimal = ZERO) -> Decimal | None:
    """Smallest x ≥ 0 (to 1/100 cent) with f(x) ≥ target, for a non-decreasing f."""
    if f(ZERO) >= target:
        return ZERO
    low, high = ZERO, Decimal(1)
    for _ in range(80):  # expand until f(high) ≥ target
        if f(high) >= target:
            break
        low, high = high, high * 2
    else:
        return None
    precision = Decimal("0.0001")
    while high - low > precision:
        mid = (low + high) / 2
        if f(mid) >= target:
            high = mid
        else:
            low = mid
    return high


def break_even_price(
    slices: tuple[Lot, ...],
    quantity: Decimal,
    rule: FeeRule,
    rate: Decimal,
    exit_markup_pct: Decimal,
    with_gst: bool,
) -> Decimal | None:
    """Lowest share price at which selling `quantity` now returns their full rupee cost."""

    def net(price: Decimal) -> Decimal:
        fees = estimate_fees(rule, "sell", quantity, price)
        return sell_shares(slices, quantity, price, fees, rate, exit_markup_pct, with_gst).net_inr

    price = solve_increasing(net)
    return None if price is None else price.quantize(Decimal("0.01"), rounding="ROUND_CEILING")


def break_even_value(cost_inr: Decimal, rate: Decimal, exit_markup_pct: Decimal, with_gst: bool):
    """Dollars that, converted now, return `cost_inr` rupees."""

    def received(usd: Decimal) -> Decimal:
        market = usd * rate
        gst = conversion_gst(market) if with_gst and usd > 0 else ZERO
        return market - market * exit_markup_pct / HUNDRED - gst

    value = solve_increasing(received, cost_inr)
    return None if value is None else value.quantize(Decimal("0.01"), rounding="ROUND_CEILING")
