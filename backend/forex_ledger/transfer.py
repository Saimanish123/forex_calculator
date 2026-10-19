"""Export / import, sample data and deleting everything."""

import csv
import io
import sqlite3
from datetime import datetime
from decimal import Decimal

from . import forex, stocks
from .calc.portfolio import LedgerError
from .config import IST
from .ledger import build

FORMAT = "forex-ledger"
VERSION = 1
# Parents before children (insert order); deleted in reverse.
TABLES = ("settings", "platforms", "banks", "bank_rates", "ticker_prices", "forex_entries",
          "stock_trades")  # fmt: skip


def export_data(conn: sqlite3.Connection) -> dict:
    tables = {}
    for table in TABLES:
        where = " WHERE origin = 'manual'" if table == "bank_rates" else ""  # auto rates refetch
        tables[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table}{where}")]
    return {
        "format": FORMAT,
        "version": VERSION,
        "schema": conn.execute("PRAGMA user_version").fetchone()[0],
        "exported_at": datetime.now(IST).isoformat(),
        "tables": tables,
    }


def import_data(conn: sqlite3.Connection, payload: dict) -> dict:
    """Replace all user data with an export. All or nothing: any problem rolls everything back."""
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ValueError("This isn't a Forex Ledger export file")
    if payload.get("version") != VERSION:
        raise ValueError(f"Unsupported export version: {payload.get('version')}")
    schema = conn.execute("PRAGMA user_version").fetchone()[0]
    if not isinstance(payload.get("schema"), int) or payload["schema"] > schema:
        raise ValueError("This export is from a newer version of the app; update the app first")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not set(tables) <= set(TABLES):
        raise ValueError("The export file is incomplete or damaged")

    counts = {}
    with conn:
        for table in reversed(TABLES):
            where = " WHERE origin = 'manual'" if table == "bank_rates" else ""
            conn.execute(f"DELETE FROM {table}{where}")
        for table in TABLES:
            rows = tables.get(table, [])
            columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for row in rows:
                if not isinstance(row, dict) or not set(row) <= columns:
                    raise ValueError(f"Unexpected data in '{table}'")
                names = list(row)
                verb = "INSERT OR REPLACE" if table == "bank_rates" else "INSERT"
                conn.execute(
                    f"{verb} INTO {table} ({', '.join(names)}) "
                    f"VALUES ({', '.join('?' * len(names))})",
                    [row[n] for n in names],
                )
            counts[table] = len(rows)
        try:
            build(conn)
        except (LedgerError, ArithmeticError, TypeError, ValueError) as exc:
            raise ValueError(f"The imported ledger isn't consistent: {exc}") from exc
    return counts


def is_empty(conn: sqlite3.Connection) -> bool:
    return not any(
        conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
        for t in ("forex_entries", "stock_trades")
    )


def delete_all(conn: sqlite3.Connection) -> None:
    """Delete every entry, trade and price. Settings, platforms and banks are kept."""
    with conn:
        for table in ("stock_trades", "forex_entries", "ticker_prices"):
            conn.execute(f"DELETE FROM {table}")


def forex_csv(conn: sqlite3.Connection) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "date_time_ist", "type", "platform", "inr", "usd", "charges_inr", "tcs_inr", "rate_got",
        "market_rate", "market_source", "markup_inr", "markup_pct", "hidden_cost_inr", "bank",
        "bank_rate", "vs_bank_inr", "ticker", "gross_usd", "withheld_usd", "note",
    ])  # fmt: skip
    for entry in reversed(forex.list_entries(conn)):
        row = forex.serialize(entry)
        if row["kind"] == "dividend":
            writer.writerow([
                row["occurred_at"], "dividend", row["platform_id"], row["income_inr"],
                row["usd_amount"], "", "", "", row["market"]["rate"], row["market"]["source"],
                "", "", "", "", "", "", row["ticker"], row["gross_usd"], row["withheld_usd"],
                row["note"] or "",
            ])  # fmt: skip
            continue
        a = row["analysis"]
        writer.writerow([
            row["occurred_at"], row["kind"], row["platform_id"], row["inr_amount"],
            row["usd_amount"], row["charges_inr"], row["tcs_inr"], a["rate_got"],
            row["market"]["rate"], row["market"]["source"], a["markup_inr"], a["markup_pct"],
            a["hidden_cost_inr"], row["bank_id"] or "", row["bank"]["rate"] or "",
            a["vs_bank_inr"] or "", "", "", "", row["note"] or "",
        ])  # fmt: skip
    return out.getvalue()


def stocks_csv(conn: sqlite3.Connection) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "date_time_ist", "type", "platform", "ticker", "quantity", "price_usd", "brokerage_usd",
        "brokerage_gst_usd", "other_fees_usd", "total_usd", "split", "qty_change", "to_ticker",
        "cost_fraction", "market_rate", "realized_gain_usd", "realized_gain_inr", "note",
    ])  # fmt: skip
    for row in reversed(stocks.list_trades(conn)):
        realized = row["realized"] or {}
        split = f"{row['split_to']}-for-{row['split_from']}" if row["kind"] == "split" else ""
        writer.writerow([
            row["occurred_at"], row["kind"], row["platform_id"], row["ticker"],
            row["quantity"] or "", row["price_usd"] or "", row["brokerage_usd"],
            row["brokerage_gst_usd"], row["other_fees_usd"], row.get("total_usd", ""), split,
            row["qty_change"] or "", row["to_ticker"] or "", row["cost_fraction"] or "",
            (row["market"] or {}).get("rate", ""), realized.get("gain_usd", ""),
            realized.get("gain_inr", ""), row["note"] or "",
        ])  # fmt: skip
    return out.getvalue()


# ---- sample data -----------------------------------------------------------------------------
# Plausible INDmoney history with fixed market and bank rates, so it loads offline and the same
# way every time. Every row is marked "Sample" in its note.

SAMPLE_FOREX = [
    # kind, time (IST), inr, usd, charges, market, bank rate (SBI)
    ("deposit", "2025-03-10T11:15", "200270", "2277.90", "270", "86.9500", "87.4500"),
    ("deposit", "2025-11-05T10:40", "100180", "1117.94", "180", "88.6500", "89.1000"),
    ("withdrawal", "2026-07-20T11:00", "65240", "700.00", "0", "94.1000", "93.6500"),
]
SAMPLE_DIVIDEND = ("2025-08-14T09:30", "VOO", "3.48", "0.87", "87.5500")
SAMPLE_TRADES = [
    # kind, time (IST), ticker, qty, price, brokerage, gst, market
    ("buy", "2025-03-11T20:05", "AAPL", "5", "236.50", "2.96", "0.53", "86.9700"),
    ("buy", "2025-03-11T20:10", "VOO", "2", "520.30", "2.60", "0.47", "86.9700"),
    ("buy", "2025-11-06T21:00", "NVDA", "5", "195.20", "2.44", "0.44", "88.7000"),
    ("sell", "2026-07-15T21:30", "AAPL", "3", "270.40", "2.03", "0.37", "93.8000"),
]
SAMPLE_PRICES = {"AAPL": "262.00", "VOO": "610.00", "NVDA": "182.00"}


def load_sample(conn: sqlite3.Connection) -> None:
    if not is_empty(conn):
        raise ValueError("Sample data can only be loaded into an empty ledger")
    now = datetime.now(IST).isoformat()
    note = "Sample"
    with conn:
        for kind, at, inr, usd, charges, market, bank in SAMPLE_FOREX:
            conn.execute(
                "INSERT INTO forex_entries (kind, occurred_at, platform_id, bank_id, inr_amount, "
                "usd_amount, charges_inr, tcs_inr, market_rate, market_source, market_bar_at, "
                "bank_rate, bank_rate_date, bank_rate_manual, note, created_at, updated_at) "
                "VALUES (?, ?, 'indmoney', 'sbi', ?, ?, ?, '0', ?, 'manual', ?, ?, ?, 1, ?, ?, ?)",
                (kind, _ist(at), inr, usd, charges, market, _ist(at), bank, at[:10], note, now,
                 now),
            )  # fmt: skip
        at, ticker, gross, withheld, market = SAMPLE_DIVIDEND
        net = f"{Decimal(gross) - Decimal(withheld):f}"
        conn.execute(
            "INSERT INTO forex_entries (kind, occurred_at, platform_id, usd_amount, ticker, "
            "gross_usd, withheld_usd, market_rate, market_source, market_bar_at, note, "
            "created_at, updated_at) VALUES ('dividend', ?, 'indmoney', ?, ?, ?, ?, ?, 'manual', "
            "?, ?, ?, ?)",
            (_ist(at), net, ticker, gross, withheld, market, _ist(at), note, now, now),
        )  # fmt: skip
        for kind, at, ticker, qty, price, brokerage, gst, market in SAMPLE_TRADES:
            conn.execute(
                "INSERT INTO stock_trades (kind, occurred_at, platform_id, ticker, quantity, "
                "price_usd, brokerage_usd, brokerage_gst_usd, market_rate, market_source, "
                "market_bar_at, note, created_at, updated_at) "
                "VALUES (?, ?, 'indmoney', ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?)",
                (kind, _ist(at), ticker, qty, price, brokerage, gst, market, _ist(at), note, now,
                 now),
            )  # fmt: skip
        for ticker, price in SAMPLE_PRICES.items():
            conn.execute(
                "INSERT OR REPLACE INTO ticker_prices VALUES (?, ?, ?, 'manual')",
                (ticker, price, now),
            )
        build(conn)  # must replay cleanly


def _ist(local: str) -> str:
    return datetime.fromisoformat(local).replace(tzinfo=IST).isoformat()
