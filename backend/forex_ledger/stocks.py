"""Stock ledger: buys, sells, splits and corporate-action adjustments.

Every change is checked by replaying the whole ledger inside the same transaction, so the stored
ledger can never contain an impossible state (e.g. selling more shares than were held, even after
an earlier buy is edited or deleted).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from . import ledger
from .calc.portfolio import LedgerError, Sale
from .config import IST
from .db import to_db
from .forex import market_fields, market_json
from .money import ZERO, D, fmt
from .rates.service import RateService
from .tickers import normalize_ticker

KINDS = ("buy", "sell", "split", "adjustment")
FUTURE_GRACE = timedelta(minutes=10)


@dataclass
class TradeInput:
    kind: str
    occurred_at: datetime
    platform_id: str
    ticker: str
    quantity: Decimal | None = None
    price_usd: Decimal | None = None
    brokerage_usd: Decimal = ZERO
    brokerage_gst_usd: Decimal = ZERO
    other_fees_usd: Decimal = ZERO
    split_from: int | None = None
    split_to: int | None = None
    qty_change: Decimal | None = None
    to_ticker: str | None = None
    cost_fraction: Decimal | None = None
    cancel_source: bool = False
    market_rate_override: Decimal | None = None
    note: str | None = None


def validate(data: TradeInput) -> None:
    if data.kind not in KINDS:
        raise ValueError("Type must be buy, sell, split or adjustment")
    if data.occurred_at > datetime.now(IST) + FUTURE_GRACE:
        raise ValueError("That time is in the future")
    data.ticker = normalize_ticker(data.ticker)
    if data.kind in ("buy", "sell"):
        if not data.quantity or data.quantity <= 0:
            raise ValueError("Enter how many shares")
        if not data.price_usd or data.price_usd <= 0:
            raise ValueError("Enter the price per share")
        if min(data.brokerage_usd, data.brokerage_gst_usd, data.other_fees_usd) < 0:
            raise ValueError("Fees can't be negative")
    elif data.kind == "split":
        if not data.split_from or not data.split_to or data.split_from <= 0 or data.split_to <= 0:
            raise ValueError("Enter the split ratio, e.g. 10 for 1")
        if data.split_from == data.split_to:
            raise ValueError("A split changes the number of shares; the ratio can't be 1 for 1")
    else:
        data.to_ticker = normalize_ticker(data.to_ticker) if data.to_ticker else None
        if data.to_ticker:
            if data.to_ticker == data.ticker:
                raise ValueError("The new ticker must differ from the old one")
            if not data.qty_change or data.qty_change <= 0:
                raise ValueError("Enter how many shares of the new ticker you received")
            if data.cost_fraction is None or not (ZERO < data.cost_fraction <= 1):
                raise ValueError("Enter the share of cost moved, above 0% and at most 100%")
            if data.cancel_source and data.cost_fraction != 1:
                raise ValueError("When the old shares are cancelled, 100% of the cost moves")
        elif not data.qty_change:
            raise ValueError("Enter the change in shares (positive or negative)")


def _fields(rates: RateService, data: TradeInput) -> dict:
    at = data.occurred_at.astimezone(IST)
    fields = {
        "kind": data.kind,
        "occurred_at": at,
        "platform_id": data.platform_id,
        "ticker": data.ticker,
        "quantity": data.quantity if data.kind in ("buy", "sell") else None,
        "price_usd": data.price_usd if data.kind in ("buy", "sell") else None,
        "brokerage_usd": data.brokerage_usd if data.kind in ("buy", "sell") else ZERO,
        "brokerage_gst_usd": data.brokerage_gst_usd if data.kind in ("buy", "sell") else ZERO,
        "other_fees_usd": data.other_fees_usd if data.kind in ("buy", "sell") else ZERO,
        "split_from": data.split_from if data.kind == "split" else None,
        "split_to": data.split_to if data.kind == "split" else None,
        "qty_change": data.qty_change if data.kind == "adjustment" else None,
        "to_ticker": data.to_ticker if data.kind == "adjustment" else None,
        "cost_fraction": data.cost_fraction
        if data.kind == "adjustment" and data.to_ticker
        else None,
        "cancel_source": bool(data.cancel_source) if data.kind == "adjustment" else False,
        "note": (data.note or "").strip() or None,
    }
    if data.kind == "split":  # a split has no money in it
        market = dict.fromkeys(
            ("market_rate", "market_source", "market_interval", "market_bar_at"), None
        ) | {"market_stale": False}
    else:
        market = market_fields(rates, at, data.market_rate_override)
    return fields | market


def _check(conn: sqlite3.Connection) -> None:
    """Replay everything (including uncommitted changes); raise if the ledger is impossible."""
    try:
        ledger.build(conn)
    except LedgerError as exc:
        raise ValueError(str(exc)) from exc


def create(conn: sqlite3.Connection, rates: RateService, data: TradeInput) -> dict:
    validate(data)
    fields = _fields(rates, data)
    now = datetime.now(IST).isoformat()
    columns = list(fields) + ["created_at", "updated_at"]
    with conn:
        cursor = conn.execute(
            f"INSERT INTO stock_trades ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})",
            [to_db(fields[c]) for c in fields] + [now, now],
        )
        _check(conn)
    return get(conn, cursor.lastrowid)


def update(conn: sqlite3.Connection, rates: RateService, trade_id: int, data: TradeInput) -> dict:
    _load(conn, trade_id)
    validate(data)
    fields = _fields(rates, data) | {"updated_at": datetime.now(IST).isoformat()}
    with conn:
        conn.execute(
            f"UPDATE stock_trades SET {', '.join(f'{c} = ?' for c in fields)} WHERE id = ?",
            [to_db(v) for v in fields.values()] + [trade_id],
        )
        _check(conn)
    return get(conn, trade_id)


def delete(conn: sqlite3.Connection, trade_id: int) -> None:
    with conn:
        if not conn.execute("DELETE FROM stock_trades WHERE id = ?", (trade_id,)).rowcount:
            raise LookupError("Trade not found")
        _check(conn)


class _Rollback(Exception):
    def __init__(self, result: dict):
        self.result = result


def preview(
    conn: sqlite3.Connection, rates: RateService, data: TradeInput, trade_id: int | None = None
) -> dict:
    """What saving would produce (exact totals, realized gain, or the reason it's impossible),
    computed by applying the change and replaying the ledger in a transaction that is always
    rolled back."""
    if trade_id is not None:
        _load(conn, trade_id)
    validate(data)
    fields = _fields(rates, data)
    now = datetime.now(IST).isoformat()
    try:
        with conn:
            if trade_id is None:
                columns = list(fields) + ["created_at", "updated_at"]
                trade_id = conn.execute(
                    f"INSERT INTO stock_trades ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' * len(columns))})",
                    [to_db(fields[c]) for c in fields] + [now, now],
                ).lastrowid
                new = True
            else:
                conn.execute(
                    f"UPDATE stock_trades SET {', '.join(f'{c} = ?' for c in fields)} WHERE id = ?",
                    [to_db(v) for v in fields.values()] + [trade_id],
                )
                new = False
            try:
                built = ledger.build(conn)
            except LedgerError as exc:
                raise ValueError(str(exc)) from exc
            sales = {s.event.id: s for s in built.sales}
            result = serialize(_load(conn, trade_id), sales.get(trade_id))
            raise _Rollback(result | {"id": None if new else trade_id})
    except _Rollback as done:
        return done.result


def get(conn: sqlite3.Connection, trade_id: int) -> dict:
    row = _load(conn, trade_id)
    sales = {s.event.id: s for s in ledger.build(conn).sales}
    return serialize(row, sales.get(trade_id))


def list_trades(conn: sqlite3.Connection) -> list[dict]:
    built = ledger.build(conn)
    sales = {s.event.id: s for s in built.sales}
    rows = conn.execute("SELECT * FROM stock_trades ORDER BY occurred_at DESC, id DESC")
    return [serialize(row, sales.get(row["id"])) for row in rows]


def serialize(row: sqlite3.Row, sale: Sale | None) -> dict:
    entry = dict(row)
    entry["market_rate"] = D(entry["market_rate"])
    entry["market_bar_at"] = (
        datetime.fromisoformat(entry["market_bar_at"]) if entry["market_bar_at"] else None
    )
    data = {
        "id": entry["id"],
        "kind": entry["kind"],
        "occurred_at": entry["occurred_at"],
        "platform_id": entry["platform_id"],
        "ticker": entry["ticker"],
        "quantity": _num(entry["quantity"]),
        "price_usd": _num(entry["price_usd"]),
        "brokerage_usd": fmt(D(entry["brokerage_usd"])),
        "brokerage_gst_usd": fmt(D(entry["brokerage_gst_usd"])),
        "other_fees_usd": fmt(D(entry["other_fees_usd"])),
        "split_from": entry["split_from"],
        "split_to": entry["split_to"],
        "qty_change": _num(entry["qty_change"]),
        "to_ticker": entry["to_ticker"],
        "cost_fraction": _num(entry["cost_fraction"]),
        "cancel_source": bool(entry["cancel_source"]),
        "note": entry["note"],
        "market": market_json(entry) if entry["market_rate"] is not None else None,
        "realized": None,
    }
    if data["kind"] in ("buy", "sell"):
        quantity, price = D(entry["quantity"]), D(entry["price_usd"])
        fees = sum(
            (D(entry[c]) for c in ("brokerage_usd", "brokerage_gst_usd", "other_fees_usd")), ZERO
        )
        data["gross_usd"] = fmt(quantity * price)
        data["fees_usd"] = fmt(fees)
        gross = quantity * price
        data["total_usd"] = fmt(gross + fees if entry["kind"] == "buy" else gross - fees)
    if sale is not None:
        data["realized"] = sale_json(sale)
    return data


def sale_json(sale: Sale) -> dict:
    return {
        "proceeds_usd": fmt(sale.proceeds),
        "cost_usd": fmt(sale.cost_usd),
        "gain_usd": fmt(sale.gain_usd),
        "gain_inr": fmt(sale.gain_inr),  # everything realized by this trade
        "stock_gain_inr": fmt(sale.stock_gain_inr),
        "currency_inr": fmt(sale.currency_inr),
        "entry_cost_inr": fmt(sale.entry_cost_inr),
        "write_off": sale.write_off,
        "inr_cost": fmt(sale.inr_cost),
        "lots": len(sale.slices),
        "first_acquired": min(s.acquired_at for s in sale.slices).isoformat(),
    }


def _num(value) -> str | None:
    """Exact decimal as entered (quantities can have up to 8 decimals)."""
    return None if value is None else f"{D(value).normalize():f}"


def _load(conn: sqlite3.Connection, trade_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM stock_trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        raise LookupError("Trade not found")
    return row
