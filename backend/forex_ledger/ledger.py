"""Loads every stored entry and trade as engine events and replays them."""

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal

from .calc.fees import FeeRule
from .calc.portfolio import Event, Ledger, replay
from .money import ZERO, D

# A month-end with no SBI rate (holiday) falls back to the latest rate within this window.
TTBR_LOOKBACK = timedelta(days=7)


def forex_events(conn: sqlite3.Connection) -> list[Event]:
    events = []
    for row in conn.execute("SELECT * FROM forex_entries"):
        at = datetime.fromisoformat(row["occurred_at"])
        common = {"at": at, "platform": row["platform_id"], "market_rate": D(row["market_rate"])}
        if row["kind"] == "deposit":
            # All-in cost: everything debited except TCS (claimable, so not a cost).
            inr = D(row["inr_amount"]) - D(row["tcs_inr"])
            events.append(
                Event("deposit", id=row["id"], usd=D(row["usd_amount"]), inr=inr, **common)
            )
        elif row["kind"] == "withdrawal":
            events.append(
                Event("withdrawal", id=row["id"], usd=D(row["usd_amount"]),
                      inr=D(row["inr_amount"]), **common)
            )  # fmt: skip
        else:
            events.append(
                Event("dividend", id=row["id"], usd=D(row["usd_amount"]), ticker=row["ticker"],
                      gross_usd=D(row["gross_usd"]), withheld_usd=D(row["withheld_usd"]), **common)
            )  # fmt: skip
    return events


def stock_events(conn: sqlite3.Connection) -> list[Event]:
    return [trade_event(row) for row in conn.execute("SELECT * FROM stock_trades")]


def trade_event(row) -> Event:
    fees = sum(
        (D(row[c]) or ZERO for c in ("brokerage_usd", "brokerage_gst_usd", "other_fees_usd")),
        ZERO,
    )
    quantity = D(row["quantity"]) if row["kind"] in ("buy", "sell") else D(row["qty_change"])
    return Event(
        row["kind"],
        at=datetime.fromisoformat(row["occurred_at"]),
        id=row["id"],
        platform=row["platform_id"],
        market_rate=D(row["market_rate"]),
        ticker=row["ticker"],
        quantity=quantity or ZERO,
        price=D(row["price_usd"]) or ZERO,
        fees=fees,
        split_from=row["split_from"],
        split_to=row["split_to"],
        to_ticker=row["to_ticker"],
        cost_fraction=D(row["cost_fraction"]),
        cancel_source=bool(row["cancel_source"]),
    )


def build(conn: sqlite3.Connection) -> Ledger:
    return replay(forex_events(conn) + stock_events(conn))


def fee_rule(conn: sqlite3.Connection, platform_id: str) -> FeeRule:
    row = conn.execute("SELECT * FROM platforms WHERE id = ?", (platform_id,)).fetchone()
    if row is None:
        raise LookupError(f"Unknown platform: {platform_id}")
    return FeeRule(
        brokerage_pct=D(row["brokerage_pct"]),
        brokerage_min_usd=D(row["brokerage_min_usd"]),
        brokerage_max_usd=D(row["brokerage_max_usd"]),
        brokerage_per_share_usd=D(row["brokerage_per_share_usd"]),
        brokerage_flat_usd=D(row["brokerage_flat_usd"]),
        brokerage_gst_pct=D(row["brokerage_gst_pct"]),
        sell_fee_usd=D(row["sell_fee_usd"]),
    )


def prices(conn: sqlite3.Connection) -> dict[str, dict]:
    return {
        row["ticker"]: {
            "price": D(row["price_usd"]),
            "as_of": row["as_of"],
            "origin": row["origin"],
        }
        for row in conn.execute("SELECT * FROM ticker_prices")
    }


def sbi_tt_buying(conn: sqlite3.Connection):
    """day → SBI TT buying rate in force that day (latest published within 7 days)."""
    rows = conn.execute(
        "SELECT rate_date, tt_buy FROM bank_rates WHERE bank_id = 'sbi' ORDER BY rate_date"
    ).fetchall()
    table = [(date.fromisoformat(r["rate_date"]), D(r["tt_buy"])) for r in rows]

    def lookup(day: date) -> Decimal | None:
        best = None
        for rate_date, rate in table:
            if rate_date > day:
                break
            best = (rate_date, rate)
        if best and day - best[0] <= TTBR_LOOKBACK:
            return best[1]
        return None

    return lookup
