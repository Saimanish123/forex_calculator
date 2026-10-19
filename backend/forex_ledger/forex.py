"""Forex ledger: deposits, withdrawals and dividends with their benchmark / bank rates."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .calc.conversions import ConversionAnalysis, analyze_conversion
from .config import IST
from .db import to_db
from .money import HUNDRED, RATE_PLACES, ZERO, D, fmt, q
from .rates.service import RateService
from .tickers import normalize_ticker

CONVERSION_KINDS = ("deposit", "withdrawal")
KINDS = (*CONVERSION_KINDS, "dividend")


@dataclass
class ForexInput:
    kind: str
    occurred_at: datetime
    platform_id: str
    bank_id: str | None = None
    inr_amount: Decimal | None = None
    usd_amount: Decimal | None = None
    charges_inr: Decimal = ZERO
    tcs_inr: Decimal = ZERO
    market_rate_override: Decimal | None = None
    bank_rate_override: Decimal | None = None
    note: str | None = None
    ticker: str | None = None  # dividends
    gross_usd: Decimal | None = None
    withheld_usd: Decimal = ZERO


def market_fields(rates: RateService, at: datetime, override: Decimal | None) -> dict:
    """The mid-market benchmark stored with an entry or trade (or the user's own rate)."""
    at = at.astimezone(IST)
    if override:
        return {
            "market_rate": override,
            "market_source": "manual",
            "market_interval": None,
            "market_bar_at": at,
            "market_stale": False,
        }
    benchmark = rates.benchmark(at)
    return {
        "market_rate": q(benchmark.rate, RATE_PLACES),
        "market_source": benchmark.source,
        "market_interval": benchmark.interval,
        "market_bar_at": benchmark.bar_at,
        "market_stale": benchmark.market_stale,
    }


def market_json(entry: dict) -> dict:
    return {
        "rate": fmt(entry["market_rate"], RATE_PLACES),
        "source": entry["market_source"],
        "interval": entry["market_interval"],
        "bar_at": entry["market_bar_at"].isoformat() if entry["market_bar_at"] else None,
        "stale": bool(entry["market_stale"]),
    }


def resolve_rates(rates: RateService, data: ForexInput) -> dict:
    """Benchmark and bank comparison rate for an entry — the stored, fetched part of an entry."""
    at = data.occurred_at.astimezone(IST)
    market = market_fields(rates, at, data.market_rate_override)

    bank = {"bank_rate": None, "bank_rate_date": None, "bank_rate_manual": False}
    if data.kind not in CONVERSION_KINDS:
        return market | bank
    if data.bank_rate_override:
        bank = {
            "bank_rate": data.bank_rate_override,
            "bank_rate_date": at.date(),
            "bank_rate_manual": True,
        }
    elif data.bank_id:
        quote = rates.bank_rate(data.bank_id, at)
        if quote:
            side = quote.tt_sell if data.kind == "deposit" else quote.tt_buy
            bank = {
                "bank_rate": q(side, RATE_PLACES),
                "bank_rate_date": quote.rate_date,
                "bank_rate_manual": False,
            }
    return market | bank


def analyze(entry: dict) -> ConversionAnalysis:
    return analyze_conversion(
        entry["kind"],
        entry["inr_amount"],
        entry["usd_amount"],
        entry["market_rate"],
        entry["charges_inr"],
        entry["tcs_inr"],
        entry["bank_rate"],
    )


def validate(data: ForexInput) -> None:
    if data.kind not in KINDS:
        raise ValueError("Type must be deposit, withdrawal or dividend")
    if data.kind == "dividend":
        if not data.ticker:
            raise ValueError("Enter the ticker that paid the dividend")
        data.ticker = normalize_ticker(data.ticker)
        if not data.gross_usd or data.gross_usd <= 0:
            raise ValueError("Enter the dividend before tax")
        if data.withheld_usd < 0 or data.withheld_usd >= data.gross_usd:
            raise ValueError("US tax withheld must be less than the dividend")
        return
    if not data.inr_amount or not data.usd_amount or data.inr_amount <= 0 or data.usd_amount <= 0:
        raise ValueError("Amounts must be greater than zero")
    if data.charges_inr < 0 or data.tcs_inr < 0:
        raise ValueError("Charges and TCS can't be negative")
    if data.kind == "deposit" and data.charges_inr + data.tcs_inr >= data.inr_amount:
        raise ValueError("Charges and TCS can't exceed the amount debited")


def preview(rates: RateService, data: ForexInput) -> dict:
    validate(data)
    entry = _input_fields(data) | resolve_rates(rates, data)
    return serialize(entry | {"id": None})


def create(conn: sqlite3.Connection, rates: RateService, data: ForexInput) -> dict:
    validate(data)
    now = datetime.now(IST).isoformat()
    fields = _input_fields(data) | resolve_rates(rates, data)
    columns = list(fields) + ["created_at", "updated_at"]
    values = [to_db(fields[c]) for c in fields] + [now, now]
    with conn:
        cursor = conn.execute(
            f"INSERT INTO forex_entries ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})",
            values,
        )
    return get(conn, cursor.lastrowid)


def update(conn: sqlite3.Connection, rates: RateService, entry_id: int, data: ForexInput) -> dict:
    get(conn, entry_id)  # 404 if missing
    validate(data)
    fields = _input_fields(data) | resolve_rates(rates, data)
    fields["updated_at"] = datetime.now(IST).isoformat()
    with conn:
        conn.execute(
            f"UPDATE forex_entries SET {', '.join(f'{c} = ?' for c in fields)} WHERE id = ?",
            [to_db(v) for v in fields.values()] + [entry_id],
        )
    return get(conn, entry_id)


def refresh_rates(conn: sqlite3.Connection, rates: RateService, entry_id: int) -> dict:
    """Re-fetch the benchmark / bank rate unless the user typed them in."""
    entry = _load(conn, entry_id)
    data = ForexInput(
        kind=entry["kind"],
        occurred_at=entry["occurred_at"],
        platform_id=entry["platform_id"],
        bank_id=entry["bank_id"],
        inr_amount=entry["inr_amount"],
        usd_amount=entry["usd_amount"],
        charges_inr=entry["charges_inr"],
        tcs_inr=entry["tcs_inr"],
        market_rate_override=entry["market_rate"] if entry["market_source"] == "manual" else None,
        bank_rate_override=entry["bank_rate"] if entry["bank_rate_manual"] else None,
        note=entry["note"],
        ticker=entry["ticker"],
        gross_usd=entry["gross_usd"],
        withheld_usd=entry["withheld_usd"] or ZERO,
    )
    return update(conn, rates, entry_id, data)


def delete(conn: sqlite3.Connection, entry_id: int) -> None:
    with conn:
        deleted = conn.execute("DELETE FROM forex_entries WHERE id = ?", (entry_id,)).rowcount
    if not deleted:
        raise LookupError("Entry not found")


def get(conn: sqlite3.Connection, entry_id: int) -> dict:
    return serialize(_load(conn, entry_id))


def list_entries(conn: sqlite3.Connection, kinds: tuple[str, ...] = KINDS) -> list[dict]:
    rows = conn.execute(
        f"SELECT * FROM forex_entries WHERE kind IN ({', '.join('?' * len(kinds))}) "
        "ORDER BY occurred_at DESC, id DESC",
        kinds,
    ).fetchall()
    return [_from_row(row) for row in rows]


def paise(result: ConversionAnalysis) -> dict:
    """Each ₹ figure of one conversion rounded to the paisa. Rows and totals both use these, so
    totals always equal the sum of the rows shown, and hidden cost = markup + charges exactly."""
    markup, charges = q(result.markup_inr), q(result.charges_inr)
    return {
        "converted_inr": q(result.converted_inr),
        "market_inr": q(result.market_inr),
        "markup_inr": markup,
        "charges_inr": charges,
        "hidden_cost_inr": markup + charges,
        "bank_inr": q(result.bank_inr),
        "vs_bank_inr": q(result.vs_bank_inr),
    }


def summary(entries: list[dict]) -> dict:
    """Conversion totals over raw (unserialized) entries: sums of the per-conversion paise
    figures. Dividends are ignored here."""
    totals = {
        kind: {
            "count": 0,
            "inr": ZERO,
            "usd": ZERO,
            "markup_inr": ZERO,
            "market_inr": ZERO,
            "charges_inr": ZERO,
        }
        for kind in CONVERSION_KINDS
    }
    tcs = vs_bank = ZERO
    compared = 0
    for entry in entries:
        if entry["kind"] not in CONVERSION_KINDS:
            continue
        money = paise(analyze(entry))
        bucket = totals[entry["kind"]]
        bucket["count"] += 1
        bucket["inr"] += q(entry["inr_amount"])
        bucket["usd"] += q(entry["usd_amount"])
        bucket["markup_inr"] += money["markup_inr"]
        bucket["market_inr"] += money["market_inr"]
        bucket["charges_inr"] += money["charges_inr"]
        tcs += q(entry["tcs_inr"])
        if money["vs_bank_inr"] is not None:
            vs_bank += money["vs_bank_inr"]
            compared += 1

    def side(bucket: dict) -> dict:
        avg = (
            bucket["markup_inr"] / bucket["market_inr"] * HUNDRED if bucket["market_inr"] else None
        )
        return {
            "count": bucket["count"],
            "inr": fmt(bucket["inr"]),
            "usd": fmt(bucket["usd"]),
            "markup_inr": fmt(bucket["markup_inr"]),
            "charges_inr": fmt(bucket["charges_inr"]),
            "avg_markup_pct": fmt(avg, 3),
        }

    hidden = sum((b["markup_inr"] + b["charges_inr"] for b in totals.values()), start=ZERO)
    return {
        "deposits": side(totals["deposit"]),
        "withdrawals": side(totals["withdrawal"]),
        "hidden_cost_inr": fmt(hidden),
        "tcs_inr": fmt(tcs),
        "vs_bank_inr": fmt(vs_bank) if compared else None,
        "vs_bank_count": compared,
    }


def serialize(entry: dict) -> dict:
    base = {
        "id": entry["id"],
        "kind": entry["kind"],
        "occurred_at": entry["occurred_at"].isoformat(),
        "platform_id": entry["platform_id"],
        "note": entry["note"],
        "market": market_json(entry),
    }
    if entry["kind"] == "dividend":
        rate = entry["market_rate"]
        return base | {
            "ticker": entry["ticker"],
            "gross_usd": fmt(entry["gross_usd"]),
            "withheld_usd": fmt(entry["withheld_usd"]),
            "usd_amount": fmt(entry["usd_amount"]),
            "income_inr": fmt(entry["usd_amount"] * rate),
            "gross_inr": fmt(entry["gross_usd"] * rate),
        }
    result = analyze(entry)
    money = paise(result)
    return base | {
        "bank_id": entry["bank_id"],
        "inr_amount": fmt(entry["inr_amount"]),
        "usd_amount": fmt(entry["usd_amount"]),
        "charges_inr": fmt(entry["charges_inr"]),
        "tcs_inr": fmt(entry["tcs_inr"]),
        "bank": {
            "rate": fmt(entry["bank_rate"], RATE_PLACES),
            "date": entry["bank_rate_date"].isoformat() if entry["bank_rate_date"] else None,
            "manual": bool(entry["bank_rate_manual"]),
        },
        "analysis": {
            **{key: fmt(value) for key, value in money.items()},
            "rate_got": fmt(result.rate_got, RATE_PLACES),
            "markup_pct": fmt(result.markup_pct, 3),
        },
    }


def _input_fields(data: ForexInput) -> dict:
    fields = {
        "kind": data.kind,
        "occurred_at": data.occurred_at.astimezone(IST),
        "platform_id": data.platform_id,
        "note": (data.note or "").strip() or None,
    }
    if data.kind == "dividend":
        return fields | {
            "bank_id": None,
            "inr_amount": None,
            "usd_amount": data.gross_usd - data.withheld_usd,
            "charges_inr": ZERO,
            "tcs_inr": ZERO,
            "ticker": data.ticker,
            "gross_usd": data.gross_usd,
            "withheld_usd": data.withheld_usd,
        }
    return fields | {
        "bank_id": data.bank_id,
        "inr_amount": data.inr_amount,
        "usd_amount": data.usd_amount,
        "charges_inr": data.charges_inr,
        "tcs_inr": data.tcs_inr,
        "ticker": None,
        "gross_usd": None,
        "withheld_usd": None,
    }


def _load(conn: sqlite3.Connection, entry_id: int) -> dict:
    row = conn.execute("SELECT * FROM forex_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise LookupError("Entry not found")
    return _from_row(row)


DECIMAL_COLUMNS = (
    "inr_amount", "usd_amount", "charges_inr", "tcs_inr", "market_rate", "bank_rate",
    "gross_usd", "withheld_usd",
)  # fmt: skip


def _from_row(row: sqlite3.Row) -> dict:
    entry = dict(row)
    for key in DECIMAL_COLUMNS:
        entry[key] = D(entry[key])
    entry["occurred_at"] = datetime.fromisoformat(entry["occurred_at"])
    if entry["market_bar_at"]:
        entry["market_bar_at"] = datetime.fromisoformat(entry["market_bar_at"])
    if entry["bank_rate_date"]:
        entry["bank_rate_date"] = datetime.fromisoformat(entry["bank_rate_date"]).date()
    return entry
