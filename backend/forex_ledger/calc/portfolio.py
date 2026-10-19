"""Replays the whole ledger in time order (SPEC §6.2).

Per platform it keeps a dollar pool at average cost — `usd` dollars, their all-in rupee cost `inr`
(markups and charges included) and `mkt`, what they were worth at mid-market when acquired — and
FIFO lots per ticker carrying the same three measures. Everything else (realized results,
holdings, scenarios) is derived from these, so the split of any rupee result into stock gain,
currency movement and costs always adds up exactly.

Pure: no database or network. Amounts are Decimals; nothing is rounded here.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from ..money import ZERO

# Same-timestamp order: money in before it's spent, sales before money out.
KIND_ORDER = {
    "deposit": 0,
    "dividend": 1,
    "buy": 2,
    "split": 3,
    "adjustment": 3,
    "sell": 4,
    "withdrawal": 5,
}


class LedgerError(ValueError):
    """The ledger is impossible as entered (e.g. selling shares that weren't held)."""

    def __init__(self, message: str, event: "Event"):
        super().__init__(message)
        self.event = event


@dataclass(frozen=True)
class Event:
    kind: str
    at: datetime
    id: int
    platform: str
    market_rate: Decimal | None = None  # ₹ per $ at the event
    usd: Decimal = ZERO  # deposit: $ credited · withdrawal: $ withdrawn · dividend: net $
    inr: Decimal = ZERO  # deposit: all-in ₹ cost (debited − TCS) · withdrawal: ₹ received
    ticker: str | None = None
    quantity: Decimal = ZERO  # buy / sell / adjustment (signed for adjustments)
    price: Decimal = ZERO  # buy / sell, $ per share
    fees: Decimal = ZERO  # buy / sell: brokerage + GST + other fees, $
    split_from: int | None = None
    split_to: int | None = None
    to_ticker: str | None = None  # adjustment moving cost basis to another ticker
    cost_fraction: Decimal | None = None
    cancel_source: bool = False
    gross_usd: Decimal = ZERO  # dividend before US withholding
    withheld_usd: Decimal = ZERO

    @property
    def order(self) -> tuple:
        return (self.at, KIND_ORDER[self.kind], self.id)


@dataclass
class Lot:
    trade_id: int
    ticker: str
    acquired_at: datetime
    qty: Decimal
    gross: Decimal  # $ paid for the shares, before fees
    fees: Decimal  # $ buy fees
    inr: Decimal  # all-in ₹ cost of the dollars spent
    mkt: Decimal  # those dollars at mid-market when acquired

    @property
    def usd_cost(self) -> Decimal:
        return self.gross + self.fees

    def split_off(self, qty: Decimal) -> "Lot":
        """Remove `qty` shares (pro-rata cost) and return them as a new lot. Remainders are
        computed by subtraction so the two parts always sum to the original exactly."""
        if qty >= self.qty:
            part = replace(self)
            self.qty = self.gross = self.fees = self.inr = self.mkt = ZERO
            return part
        f = qty / self.qty
        part = replace(
            self, qty=qty, gross=self.gross * f, fees=self.fees * f, inr=self.inr * f,
            mkt=self.mkt * f,
        )  # fmt: skip
        self.qty -= qty
        self.gross -= part.gross
        self.fees -= part.fees
        self.inr -= part.inr
        self.mkt -= part.mkt
        return part


@dataclass
class Pool:
    usd: Decimal = ZERO
    inr: Decimal = ZERO
    mkt: Decimal = ZERO

    def add(self, usd: Decimal, inr: Decimal, mkt: Decimal) -> None:
        self.usd += usd
        self.inr += inr
        self.mkt += mkt

    def take(self, usd: Decimal, rate: Decimal | None) -> tuple[Decimal, Decimal, Decimal]:
        """Spend `usd` at average cost → (₹ cost, market basis, uncovered $). Dollars beyond the
        balance (deposits not recorded) are valued at `rate` with no markup."""
        covered = min(usd, max(self.usd, ZERO))
        if covered == self.usd:
            inr, mkt = self.inr, self.mkt
            self.usd = self.inr = self.mkt = ZERO
        elif covered > 0:
            f = covered / self.usd
            inr, mkt = self.inr * f, self.mkt * f
            self.usd -= covered
            self.inr -= inr
            self.mkt -= mkt
        else:
            inr = mkt = ZERO
        uncovered = usd - covered
        if uncovered > 0:
            if rate is None:
                raise ValueError("A market rate is needed to value dollars not covered by deposits")
            inr += uncovered * rate
            mkt += uncovered * rate
        return inr, mkt, uncovered


@dataclass
class PlatformState:
    pool: Pool = field(default_factory=Pool)
    lots: dict[str, list[Lot]] = field(default_factory=dict)

    def held(self, ticker: str) -> Decimal:
        return sum((lot.qty for lot in self.lots.get(ticker, [])), ZERO)


@dataclass(frozen=True)
class Sale:
    """A sell (or a write-off adjustment, with zero proceeds)."""

    event: Event
    gross: Decimal  # S: quantity × price
    fees: Decimal  # sell fees
    slices: tuple[Lot, ...]  # FIFO lots consumed

    @property
    def proceeds(self) -> Decimal:
        return self.gross - self.fees

    @property
    def cost_usd(self) -> Decimal:
        return sum((s.usd_cost for s in self.slices), ZERO)

    @property
    def buy_gross(self) -> Decimal:
        return sum((s.gross for s in self.slices), ZERO)

    @property
    def buy_fees(self) -> Decimal:
        return sum((s.fees for s in self.slices), ZERO)

    @property
    def inr_cost(self) -> Decimal:
        return sum((s.inr for s in self.slices), ZERO)

    @property
    def mkt_cost(self) -> Decimal:
        return sum((s.mkt for s in self.slices), ZERO)

    @property
    def gain_usd(self) -> Decimal:
        """Net of buy and sell fees."""
        return self.proceeds - self.cost_usd

    @property
    def write_off(self) -> bool:
        """Shares removed with no proceeds: nothing goes back to the dollar pool."""
        return self.event.kind == "adjustment"

    @property
    def stock_gain_inr(self) -> Decimal:
        """The $ gain/loss at the sale's market rate."""
        return self.gain_usd * self.event.market_rate

    @property
    def currency_inr(self) -> Decimal:
        """A sale's proceeds carry its currency movement and entry costs back into the pool (they
        are realized when those dollars are withdrawn). A write-off has no proceeds to carry
        them, so it realizes them now."""
        if not self.write_off:
            return ZERO
        return self.cost_usd * self.event.market_rate - self.mkt_cost

    @property
    def entry_cost_inr(self) -> Decimal:
        return self.inr_cost - self.mkt_cost if self.write_off else ZERO

    @property
    def gain_inr(self) -> Decimal:
        """Everything this trade realized in rupees (a write-off: minus its full rupee cost)."""
        return self.stock_gain_inr + self.currency_inr - self.entry_cost_inr


@dataclass(frozen=True)
class Withdrawal:
    event: Event
    cost_inr: Decimal  # share of the pool's all-in ₹ cost withdrawn
    cost_mkt: Decimal  # share of the pool's market basis withdrawn
    uncovered_usd: Decimal

    @property
    def market_inr(self) -> Decimal:
        return self.event.usd * self.event.market_rate

    @property
    def currency_inr(self) -> Decimal:
        return self.market_inr - self.cost_mkt

    @property
    def entry_cost_inr(self) -> Decimal:
        """Markups and charges paid when these dollars came in."""
        return self.cost_inr - self.cost_mkt

    @property
    def exit_cost_inr(self) -> Decimal:
        """Markup and charges on this withdrawal."""
        return self.market_inr - self.event.inr

    @property
    def net_inr(self) -> Decimal:
        """= currency − entry cost − exit cost."""
        return self.event.inr - self.cost_inr


@dataclass(frozen=True)
class Dividend:
    event: Event

    @property
    def income_inr(self) -> Decimal:
        return self.event.usd * self.event.market_rate


@dataclass
class Ledger:
    platforms: dict[str, PlatformState] = field(default_factory=dict)
    sales: list[Sale] = field(default_factory=list)
    withdrawals: list[Withdrawal] = field(default_factory=list)
    dividends: list[Dividend] = field(default_factory=list)
    uncovered: list[tuple[Event, Decimal]] = field(default_factory=list)  # (event, $ not covered)

    def platform(self, platform_id: str) -> PlatformState:
        return self.platforms.setdefault(platform_id, PlatformState())


def replay(events: Iterable[Event]) -> Ledger:
    ledger = Ledger()
    for event in sorted(events, key=lambda e: e.order):
        state = ledger.platform(event.platform)
        handler = _HANDLERS[event.kind]
        handler(ledger, state, event)
    return ledger


def _deposit(ledger: Ledger, state: PlatformState, e: Event) -> None:
    state.pool.add(e.usd, e.inr, e.usd * e.market_rate)


def _dividend(ledger: Ledger, state: PlatformState, e: Event) -> None:
    value = e.usd * e.market_rate
    state.pool.add(e.usd, value, value)
    ledger.dividends.append(Dividend(e))


def _withdrawal(ledger: Ledger, state: PlatformState, e: Event) -> None:
    inr, mkt, uncovered = state.pool.take(e.usd, e.market_rate)
    if uncovered:
        ledger.uncovered.append((e, uncovered))
    ledger.withdrawals.append(Withdrawal(e, inr, mkt, uncovered))


def _buy(ledger: Ledger, state: PlatformState, e: Event) -> None:
    gross = e.quantity * e.price
    inr, mkt, uncovered = state.pool.take(gross + e.fees, e.market_rate)
    if uncovered:
        ledger.uncovered.append((e, uncovered))
    lot = Lot(e.id, e.ticker, e.at, e.quantity, gross, e.fees, inr, mkt)
    state.lots.setdefault(e.ticker, []).append(lot)


def _consume(state: PlatformState, e: Event, ticker: str, qty: Decimal) -> tuple[Lot, ...]:
    held = state.held(ticker)
    if qty > held:
        verb = "sold" if e.kind == "sell" else "written off"
        raise LedgerError(
            f"Only {_qty(held)} shares of {ticker} were held on {e.at:%d %b %Y}, "
            f"so {_qty(qty)} can't be {verb}",
            e,
        )
    lots, slices, remaining = state.lots[ticker], [], qty
    while remaining > 0:
        part = lots[0].split_off(remaining)
        slices.append(part)
        remaining -= part.qty
        if lots[0].qty == 0:
            lots.pop(0)
    return tuple(slices)


def _sell(ledger: Ledger, state: PlatformState, e: Event) -> None:
    slices = _consume(state, e, e.ticker, e.quantity)
    sale = Sale(e, e.quantity * e.price, e.fees, slices)
    # Proceeds join the pool with the lots' rupee cost plus the gain valued today, so the
    # position's currency movement and entry costs travel on with those dollars.
    gain_inr = sale.stock_gain_inr
    state.pool.add(sale.proceeds, sale.inr_cost + gain_inr, sale.mkt_cost + gain_inr)
    ledger.sales.append(sale)


def _split(ledger: Ledger, state: PlatformState, e: Event) -> None:
    lots = state.lots.get(e.ticker)
    if not lots:
        raise LedgerError(f"No {e.ticker} shares were held on {e.at:%d %b %Y} to split", e)
    ratio = Decimal(e.split_to) / Decimal(e.split_from)
    for lot in lots:
        lot.qty *= ratio


def _adjustment(ledger: Ledger, state: PlatformState, e: Event) -> None:
    if e.to_ticker:
        _transfer(state, e)
    elif e.quantity > 0:  # shares received at zero cost (e.g. bonus shares)
        lot = Lot(e.id, e.ticker, e.at, e.quantity, ZERO, ZERO, ZERO, ZERO)
        state.lots.setdefault(e.ticker, []).append(lot)
    elif e.quantity < 0:  # shares written off with no proceeds (e.g. delisting)
        # Fully realized now (no dollars come back to carry any of it): −(the lots' rupee cost).
        slices = _consume(state, e, e.ticker, -e.quantity)
        ledger.sales.append(Sale(e, ZERO, ZERO, slices))


def _transfer(state: PlatformState, e: Event) -> None:
    """Move `cost_fraction` of each open lot's cost (and acquisition date) to `to_ticker`,
    receiving `quantity` new shares spread over the lots by quantity."""
    lots = state.lots.get(e.ticker)
    if not lots:
        raise LedgerError(f"No {e.ticker} shares were held on {e.at:%d %b %Y}", e)
    f = e.cost_fraction
    if not (ZERO < f <= 1) or (e.cancel_source and f != 1):
        # Cancelling the old shares while keeping part of their cost would lose that cost.
        raise LedgerError(
            "Cost moved must be above 0 and at most 100% (exactly 100% for a merger)", e
        )
    total_qty = sum((lot.qty for lot in lots), ZERO)
    new_lots, allocated = [], ZERO
    for i, lot in enumerate(lots):
        qty = e.quantity - allocated if i == len(lots) - 1 else e.quantity * lot.qty / total_qty
        allocated += qty
        moved = Lot(
            lot.trade_id, e.to_ticker, lot.acquired_at, qty,
            lot.gross * f, lot.fees * f, lot.inr * f, lot.mkt * f,
        )  # fmt: skip
        lot.gross -= moved.gross
        lot.fees -= moved.fees
        lot.inr -= moved.inr
        lot.mkt -= moved.mkt
        new_lots.append(moved)
    if e.cancel_source:
        del state.lots[e.ticker]
    target = state.lots.setdefault(e.to_ticker, [])
    target.extend(new_lots)
    target.sort(key=lambda lot: lot.acquired_at)


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


_HANDLERS = {
    "deposit": _deposit,
    "dividend": _dividend,
    "withdrawal": _withdrawal,
    "buy": _buy,
    "sell": _sell,
    "split": _split,
    "adjustment": _adjustment,
}
