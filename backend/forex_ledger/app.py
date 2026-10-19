"""FastAPI app: JSON API under /api, the frontend served from /."""

import logging
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, db, forex, insights, ledger, stocks, transfer
from .calc.fees import estimate_fees
from .config import FRONTEND_DIR, IST
from .money import HUNDRED, fmt
from .rates.service import AUTO_SOURCES, RateService, RateUnavailable
from .rates.sources import RateSources
from .schemas import (
    BankIn,
    BankRateIn,
    CalculatorIn,
    DeleteAllIn,
    FeeEstimateIn,
    ForexEntryIn,
    PlatformIn,
    PriceIn,
    SettingsIn,
    TradeIn,
)
from .tickers import normalize_ticker

PLATFORM_FIELDS = list(PlatformIn.model_fields)

log = logging.getLogger(__name__)


class FrontendFiles(StaticFiles):
    """Static files the browser must revalidate on every load (cheap 304s), so an updated app
    never runs stale JavaScript from the browser cache."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _warm_up_rates() -> None:
    conn = db.connect()
    try:
        RateService(conn, RateSources(timeout=60, thorough=True)).warm_up()
    except Exception:  # best effort; requests fetch lazily anyway
        log.exception("Rate warm-up failed")
    finally:
        conn.close()


def create_app(sources=None, warm_up: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect()
        db.migrate(conn)
        conn.close()
        if warm_up:
            threading.Thread(target=_warm_up_rates, name="rate-warm-up", daemon=True).start()
        yield

    app = FastAPI(title="Forex Ledger", version=__version__, lifespan=lifespan)
    app.state.sources = sources or RateSources()

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(LookupError)
    async def not_found(_: Request, exc: LookupError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(RateUnavailable)
    async def rate_unavailable(_: Request, exc: RateUnavailable):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity(_: Request, exc: sqlite3.IntegrityError):
        return JSONResponse({"detail": "Unknown platform or bank"}, status_code=400)

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = db.connect()
        try:
            yield conn
        finally:
            conn.close()

    def get_rates(conn: sqlite3.Connection = Depends(get_conn)) -> RateService:
        return RateService(conn, app.state.sources)

    # ---- meta & settings -----------------------------------------------------------------

    @app.get("/api/meta")
    def meta(conn: sqlite3.Connection = Depends(get_conn)):
        return {
            "version": __version__,
            "settings": _settings(conn),
            "platforms": [
                dict(row)
                for row in conn.execute("SELECT * FROM platforms ORDER BY sort_order, name")
            ],
            "banks": [
                dict(row) | {"auto": row["rate_source"] in AUTO_SOURCES}
                for row in conn.execute("SELECT * FROM banks ORDER BY sort_order, name")
            ],
            "database": str(db.db_path()),
            "empty": transfer.is_empty(conn),
        }

    @app.put("/api/settings")
    def update_settings(body: SettingsIn, conn: sqlite3.Connection = Depends(get_conn)):
        values = body.model_dump(exclude_unset=True)
        if values.get("default_platform"):
            _require(conn, "platforms", values["default_platform"])
        if values.get("default_bank"):
            _require(conn, "banks", values["default_bank"])
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [(key, _setting_value(value)) for key, value in values.items()],
            )
        return _settings(conn)

    @app.post("/api/platforms", status_code=201)
    def create_platform(body: PlatformIn, conn: sqlite3.Connection = Depends(get_conn)):
        platform_id = _unique_id(conn, "platforms", body.name)
        values = _db_values(body)
        with conn:
            conn.execute(
                f"INSERT INTO platforms (id, {', '.join(PLATFORM_FIELDS)}) "
                f"VALUES (?, {', '.join('?' * len(PLATFORM_FIELDS))})",
                [platform_id, *values],
            )
        return dict(conn.execute("SELECT * FROM platforms WHERE id = ?", (platform_id,)).fetchone())

    @app.put("/api/platforms/{platform_id}")
    def update_platform(
        platform_id: str, body: PlatformIn, conn: sqlite3.Connection = Depends(get_conn)
    ):
        _require(conn, "platforms", platform_id)
        with conn:
            conn.execute(
                f"UPDATE platforms SET {', '.join(f'{f} = ?' for f in PLATFORM_FIELDS)} "
                "WHERE id = ?",
                [*_db_values(body), platform_id],
            )
        return dict(conn.execute("SELECT * FROM platforms WHERE id = ?", (platform_id,)).fetchone())

    @app.post("/api/banks", status_code=201)
    def create_bank(body: BankIn, conn: sqlite3.Connection = Depends(get_conn)):
        bank_id = _unique_id(conn, "banks", body.name)
        with conn:
            conn.execute(
                "INSERT INTO banks (id, name, rate_source) VALUES (?, ?, 'manual')",
                (bank_id, body.name.strip()),
            )
        return dict(conn.execute("SELECT * FROM banks WHERE id = ?", (bank_id,)).fetchone()) | {
            "auto": False
        }

    @app.post("/api/backup")
    def backup_now(conn: sqlite3.Connection = Depends(get_conn)):
        path = db.backup(conn, _settings(conn).get("backup_dir"))
        return {"path": str(path)}

    # ---- rates ---------------------------------------------------------------------------

    @app.get("/api/rates/benchmark")
    def benchmark(at: datetime, rates: RateService = Depends(get_rates)):
        at = at if at.tzinfo else at.replace(tzinfo=IST)
        return insights.benchmark_json(rates.benchmark(at))

    @app.get("/api/rates/today")
    def rates_today(
        conn: sqlite3.Connection = Depends(get_conn), rates: RateService = Depends(get_rates)
    ):
        try:
            mid = rates.benchmark(datetime.now(IST))
        except RateUnavailable:
            mid = None
        banks = []
        for bank in conn.execute("SELECT * FROM banks ORDER BY sort_order, name").fetchall():
            quote = rates.latest_bank_rate(bank["id"])
            status = rates.fetch_status(bank["rate_source"])
            banks.append(
                {
                    "id": bank["id"],
                    "name": bank["name"],
                    "auto": bank["rate_source"] in AUTO_SOURCES,
                    "status": dict(status) if status else None,
                    "rate": _bank_quote_json(quote, mid),
                }
            )
        return {
            "mid": insights.benchmark_json(mid),
            "banks": banks,
            "platforms": _platform_history(conn),
        }

    @app.post("/api/bank-rates", status_code=201)
    def save_bank_rate(body: BankRateIn, rates: RateService = Depends(get_rates)):
        rates.save_manual_bank_rate(body.bank_id, body.rate_date, body.tt_buy, body.tt_sell)
        return {"ok": True}

    # ---- forex ledger --------------------------------------------------------------------

    @app.get("/api/forex-entries")
    def list_forex(conn: sqlite3.Connection = Depends(get_conn)):
        entries = forex.list_entries(conn)
        return {
            "entries": [forex.serialize(entry) for entry in entries],
            "summary": forex.summary(entries),
        }

    @app.post("/api/forex-entries/preview")
    def preview_forex(body: ForexEntryIn, rates: RateService = Depends(get_rates)):
        return forex.preview(rates, _forex_input(body))

    @app.post("/api/forex-entries", status_code=201)
    def create_forex(
        body: ForexEntryIn,
        conn: sqlite3.Connection = Depends(get_conn),
        rates: RateService = Depends(get_rates),
    ):
        return forex.create(conn, rates, _forex_input(body))

    @app.put("/api/forex-entries/{entry_id}")
    def update_forex(
        entry_id: int,
        body: ForexEntryIn,
        conn: sqlite3.Connection = Depends(get_conn),
        rates: RateService = Depends(get_rates),
    ):
        return forex.update(conn, rates, entry_id, _forex_input(body))

    @app.post("/api/forex-entries/{entry_id}/refresh-rates")
    def refresh_forex(
        entry_id: int,
        conn: sqlite3.Connection = Depends(get_conn),
        rates: RateService = Depends(get_rates),
    ):
        return forex.refresh_rates(conn, rates, entry_id)

    @app.delete("/api/forex-entries/{entry_id}", status_code=204)
    def delete_forex(entry_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        forex.delete(conn, entry_id)

    # ---- stocks ---------------------------------------------------------------------------

    @app.get("/api/stock-trades")
    def list_trades(conn: sqlite3.Connection = Depends(get_conn)):
        return {"trades": stocks.list_trades(conn)}

    @app.post("/api/stock-trades/fees")
    def fee_estimate(body: FeeEstimateIn, conn: sqlite3.Connection = Depends(get_conn)):
        fees = estimate_fees(
            ledger.fee_rule(conn, body.platform_id), body.side, body.quantity, body.price_usd
        )
        return {
            "brokerage_usd": fmt(fees.brokerage_usd),
            "brokerage_gst_usd": fmt(fees.brokerage_gst_usd),
            "other_fees_usd": fmt(fees.other_fees_usd),
            "total_usd": fmt(fees.total),
        }

    @app.post("/api/stock-trades/preview")
    def preview_trade(
        body: TradeIn,
        trade_id: int | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
        rates: RateService = Depends(get_rates),
    ):
        return stocks.preview(conn, rates, stocks.TradeInput(**body.model_dump()), trade_id)

    @app.post("/api/stock-trades", status_code=201)
    def create_trade(
        body: TradeIn,
        conn: sqlite3.Connection = Depends(get_conn),
        rates: RateService = Depends(get_rates),
    ):
        return stocks.create(conn, rates, stocks.TradeInput(**body.model_dump()))

    @app.put("/api/stock-trades/{trade_id}")
    def update_trade(
        trade_id: int,
        body: TradeIn,
        conn: sqlite3.Connection = Depends(get_conn),
        rates: RateService = Depends(get_rates),
    ):
        return stocks.update(conn, rates, trade_id, stocks.TradeInput(**body.model_dump()))

    @app.delete("/api/stock-trades/{trade_id}", status_code=204)
    def delete_trade(trade_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        stocks.delete(conn, trade_id)

    @app.get("/api/holdings")
    def holdings(conn: sqlite3.Connection = Depends(get_conn)):
        return {"platforms": insights.holdings(conn)}

    @app.put("/api/prices/{ticker}")
    def set_price(ticker: str, body: PriceIn, conn: sqlite3.Connection = Depends(get_conn)):
        return _save_price(conn, normalize_ticker(ticker), body.price_usd, "manual")

    @app.post("/api/prices/{ticker}/fetch")
    def fetch_price(ticker: str, conn: sqlite3.Connection = Depends(get_conn)):
        ticker = normalize_ticker(ticker)
        try:
            price, as_of = app.state.sources.stock_price(ticker)
        except (LookupError, ValueError):
            raise
        except Exception as exc:
            raise RateUnavailable(f"Couldn't fetch a price for {ticker} right now") from exc
        return _save_price(conn, ticker, price, "auto", as_of)

    @app.post("/api/calculator")
    def calculator(
        body: CalculatorIn,
        conn: sqlite3.Connection = Depends(get_conn),
        rates: RateService = Depends(get_rates),
    ):
        return insights.calculator(conn, rates, body)

    @app.get("/api/dashboard")
    def dashboard(
        conn: sqlite3.Connection = Depends(get_conn), rates: RateService = Depends(get_rates)
    ):
        return insights.dashboard(conn, rates)

    # ---- data ----------------------------------------------------------------------------

    @app.get("/api/export")
    def export(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(
            transfer.export_data(conn),
            headers=_download(f"forex-ledger-{datetime.now(IST):%Y%m%d}.json"),
        )

    @app.get("/api/export/{name}.csv")
    def export_csv(name: str, conn: sqlite3.Connection = Depends(get_conn)):
        writers = {"conversions": transfer.forex_csv, "trades": transfer.stocks_csv}
        if name not in writers:
            raise LookupError("Unknown export")
        return Response(
            writers[name](conn),
            media_type="text/csv; charset=utf-8",
            headers=_download(f"forex-ledger-{name}-{datetime.now(IST):%Y%m%d}.csv"),
        )

    @app.post("/api/import")
    def import_(payload: dict = Body(...), conn: sqlite3.Connection = Depends(get_conn)):
        backup = db.backup(conn, _settings(conn).get("backup_dir"))  # undo point
        return {"imported": transfer.import_data(conn, payload), "backup": str(backup)}

    @app.post("/api/sample-data", status_code=201)
    def sample_data(conn: sqlite3.Connection = Depends(get_conn)):
        transfer.load_sample(conn)
        return {"ok": True}

    @app.post("/api/delete-all")
    def delete_all(body: DeleteAllIn, conn: sqlite3.Connection = Depends(get_conn)):
        backup = db.backup(conn, _settings(conn).get("backup_dir"))  # undo point
        transfer.delete_all(conn)
        return {"backup": str(backup)}

    if FRONTEND_DIR.exists():
        app.mount("/", FrontendFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


def _forex_input(body: ForexEntryIn) -> forex.ForexInput:
    return forex.ForexInput(**body.model_dump())


def _setting_value(value) -> str | None:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Decimal):
        return f"{value:f}"
    return (value or "").strip() or None


def _save_price(conn, ticker: str, price: Decimal, origin: str, as_of: datetime | None = None):
    as_of = (as_of or datetime.now(IST)).isoformat()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO ticker_prices (ticker, price_usd, as_of, origin) "
            "VALUES (?, ?, ?, ?)",
            (ticker, f"{price:f}", as_of, origin),
        )
    return {"ticker": ticker, "price_usd": fmt(price, 4), "as_of": as_of, "origin": origin}


def _download(filename: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def _settings(conn: sqlite3.Connection) -> dict:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}


def _require(conn: sqlite3.Connection, table: str, row_id: str) -> None:
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone():
        raise LookupError(f"Unknown {table[:-1]}: {row_id}")


def _unique_id(conn: sqlite3.Connection, table: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or table[:-1]
    candidate, n = base, 2
    while conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (candidate,)).fetchone():
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def _db_values(body: PlatformIn) -> list:
    values = body.model_dump()
    return [f"{v:f}" if isinstance(v, Decimal) else v for v in values.values()]


def _bank_quote_json(quote, mid) -> dict | None:
    if quote is None:
        return None
    data = {
        "date": quote.rate_date.isoformat(),
        "published_at": quote.published_at.isoformat(),
        "tt_buy": fmt(quote.tt_buy, 4),
        "tt_sell": fmt(quote.tt_sell, 4),
        "origin": quote.origin,
        # Buying $1 and selling it back the same day returns tt_buy / tt_sell of the rupees.
        "round_trip_pct": fmt((1 - quote.tt_buy / quote.tt_sell) * HUNDRED, 3),
        "buy_usd_markup_pct": None,
        "sell_usd_markup_pct": None,
    }
    if mid:
        data["buy_usd_markup_pct"] = fmt((quote.tt_sell / mid.rate - 1) * HUNDRED, 3)
        data["sell_usd_markup_pct"] = fmt((1 - quote.tt_buy / mid.rate) * HUNDRED, 3)
    return data


def _platform_history(conn: sqlite3.Connection) -> list[dict]:
    """Average markup you actually paid per platform, from your own ledger."""
    by_platform: dict[str, list[dict]] = {}
    for entry in forex.list_entries(conn, forex.CONVERSION_KINDS):
        by_platform.setdefault(entry["platform_id"], []).append(entry)
    names = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM platforms")}
    return [
        {"id": platform_id, "name": names.get(platform_id, platform_id)} | forex.summary(entries)
        for platform_id, entries in by_platform.items()
    ]
