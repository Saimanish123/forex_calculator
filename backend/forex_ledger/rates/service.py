"""Mid-market benchmark and bank rate lookup with local caching (SPEC §5)."""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ..config import IST
from ..money import D
from .parsers import BankQuote, Bar

log = logging.getLogger(__name__)

# (interval, seconds, max age in days that Yahoo serves this interval for)
YAHOO_INTERVALS = (("1m", 60, 29), ("5m", 300, 59), ("1h", 3600, 729))
# How far back to fetch, so weekend / holiday entries still find the last close.
LOOKBACK = {"1m": timedelta(days=3), "5m": timedelta(days=4), "1h": timedelta(days=6)}
# Bank rates older than this (relative to the entry) aren't used for comparison.
BANK_RATE_MAX_AGE = timedelta(days=4)
SBI_REFETCH_AFTER = timedelta(hours=3)
SNAPSHOT_REFETCH_AFTER = timedelta(hours=6)
RETRY_FAILED_AFTER = timedelta(minutes=10)
# Banks whose adapter fetches only the current rate sheet: rate_source → RateSources method.
SNAPSHOT_SOURCES = {"icici": "icici_quote", "hdfc": "hdfc_quote"}
AUTO_SOURCES = ("sbi", *SNAPSHOT_SOURCES)


class RateUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Benchmark:
    rate: Decimal
    source: str  # 'yahoo' | 'frankfurter' | 'open_er'
    interval: str  # '1m' | '5m' | '1h' | '1d'
    bar_at: datetime
    market_stale: bool


@dataclass(frozen=True)
class BankRate:
    bank_id: str
    rate_date: date
    published_at: datetime
    tt_buy: Decimal
    tt_sell: Decimal
    origin: str


def pick_bar(bars: list[Bar], at_ts: int) -> Bar | None:
    """The last bar that started at or before `at_ts`."""
    candidates = [bar for bar in bars if bar.ts <= at_ts]
    return max(candidates, key=lambda bar: bar.ts) if candidates else None


def is_quote_stale(at_ts: int, bar_ts: int, interval_seconds: int) -> bool:
    return at_ts - bar_ts > max(1800, 2 * interval_seconds)


class RateService:
    def __init__(self, conn: sqlite3.Connection, sources, now=lambda: datetime.now(IST)):
        self.conn = conn
        self.sources = sources
        self.now = now

    # ---- mid-market ----------------------------------------------------------------------

    def benchmark(self, at: datetime) -> Benchmark:
        now = self.now()
        if at > now + timedelta(minutes=10):
            raise ValueError("That time is in the future")
        at_ts = int(at.timestamp())

        # The finest bar already cached for this exact moment wins, whatever the entry's age —
        # so editing an old entry never downgrades it from 1-minute to coarser data.
        for interval, seconds, _ in YAHOO_INTERVALS:
            bar = self._cached_bar("yahoo", interval, at_ts)
            if bar and at_ts - bar.ts < seconds:
                return _yahoo_benchmark(bar, interval, seconds, at_ts)

        age_days = (now - at).total_seconds() / 86400
        for interval, seconds, max_age in YAHOO_INTERVALS:
            if age_days <= max_age:
                benchmark = self._fetch_yahoo(at, interval, seconds, now)
                if benchmark:
                    return benchmark
                break
        return self._daily(at, now)

    def _fetch_yahoo(
        self, at: datetime, interval: str, seconds: int, now: datetime
    ) -> Benchmark | None:
        at_ts, now_ts = int(at.timestamp()), int(now.timestamp())
        start = int((at - LOOKBACK[interval]).timestamp())
        try:
            bars = self.sources.yahoo_bars(interval, start, min(at_ts + seconds, now_ts))
        except Exception as exc:  # network / format problems → fall back
            log.warning("Yahoo %s fetch failed: %s", interval, exc)
            return None
        # Only finished bars are cached: the current bar's close still moves until it ends.
        self._store_bars("yahoo", interval, [b for b in bars if b.ts + seconds <= now_ts])
        bar = pick_bar(bars, at_ts)
        return _yahoo_benchmark(bar, interval, seconds, at_ts) if bar else None

    def _daily(self, at: datetime, now: datetime) -> Benchmark:
        day = at.astimezone(IST).date()
        if day == now.date():
            try:
                updated, rate = self.sources.open_er_latest()
                return Benchmark(rate, "open_er", "1d", updated, at.weekday() >= 5)
            except Exception as exc:
                log.warning("open.er-api fetch failed: %s", exc)

        cache_ts = _day_ts(day)
        cached = self.conn.execute(
            "SELECT close, fix_ts FROM fx_bars "
            "WHERE source = 'frankfurter' AND interval = '1d' AND ts = ?",
            (cache_ts,),
        ).fetchone()
        if cached:
            rate = D(cached["close"])
            fix_day = datetime.fromtimestamp(cached["fix_ts"], UTC).date()
        else:
            try:
                fix_day, rate = self.sources.frankfurter_daily(day)
            except Exception as exc:
                raise RateUnavailable("Couldn't fetch a market rate for that date") from exc
            if day < now.date():  # today's fix may not be published yet
                with self.conn:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO fx_bars (source, interval, ts, close, fix_ts) "
                        "VALUES ('frankfurter', '1d', ?, ?, ?)",
                        (cache_ts, f"{rate:f}", _day_ts(fix_day)),
                    )
        bar_at = datetime(fix_day.year, fix_day.month, fix_day.day, 16, 0, tzinfo=IST)
        return Benchmark(rate, "frankfurter", "1d", bar_at, fix_day != day or day.weekday() >= 5)

    def _cached_bar(self, source: str, interval: str, at_ts: int) -> Bar | None:
        row = self.conn.execute(
            "SELECT ts, close FROM fx_bars WHERE source = ? AND interval = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            (source, interval, at_ts),
        ).fetchone()
        return Bar(row["ts"], D(row["close"])) if row else None

    def _store_bars(self, source: str, interval: str, bars: list[Bar]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO fx_bars (source, interval, ts, close) VALUES (?, ?, ?, ?)",
                [(source, interval, bar.ts, f"{bar.close:f}") for bar in bars],
            )

    # ---- banks ---------------------------------------------------------------------------

    def bank_rate(self, bank_id: str, at: datetime) -> BankRate | None:
        """The bank's published rate in force at `at` (None if unknown)."""
        day = at.astimezone(IST).date()
        self._refresh_bank(bank_id, day)
        row = self.conn.execute(
            "SELECT * FROM bank_rates WHERE bank_id = ? AND rate_date <= ? AND rate_date >= ? "
            "AND published_at <= ? ORDER BY rate_date DESC LIMIT 1",
            (
                bank_id,
                day.isoformat(),
                (day - BANK_RATE_MAX_AGE).isoformat(),
                at.astimezone(IST).replace(microsecond=0).isoformat(),
            ),
        ).fetchone()
        return _bank_rate(row) if row else None

    def latest_bank_rate(self, bank_id: str) -> BankRate | None:
        self._refresh_bank(bank_id, self.now().date())
        row = self.conn.execute(
            "SELECT * FROM bank_rates WHERE bank_id = ? ORDER BY rate_date DESC LIMIT 1",
            (bank_id,),
        ).fetchone()
        return _bank_rate(row) if row else None

    def save_manual_bank_rate(
        self, bank_id: str, day: date, tt_buy: Decimal, tt_sell: Decimal
    ) -> None:
        published = datetime(day.year, day.month, day.day, tzinfo=IST)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO bank_rates "
                "(bank_id, rate_date, published_at, tt_buy, tt_sell, origin) "
                "VALUES (?, ?, ?, ?, ?, 'manual')",
                (bank_id, day.isoformat(), published.isoformat(), f"{tt_buy:f}", f"{tt_sell:f}"),
            )

    def fetch_status(self, source: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM fetch_log WHERE source = ?", (source,)).fetchone()

    def _refresh_bank(self, bank_id: str, day: date) -> None:
        bank = self.conn.execute(
            "SELECT rate_source FROM banks WHERE id = ?", (bank_id,)
        ).fetchone()
        if bank is None:
            raise ValueError(f"Unknown bank: {bank_id}")
        source = bank["rate_source"]
        if source == "sbi":
            have = self.conn.execute(
                "SELECT 1 FROM bank_rates WHERE bank_id = ? AND rate_date >= ?",
                (bank_id, day.isoformat()),
            ).fetchone()
            if not have and self._due("sbi", SBI_REFETCH_AFTER):
                self._fetch(bank_id, "sbi", self.sources.sbi_quotes)
        elif source in SNAPSHOT_SOURCES:
            # These banks only publish their current sheet, so history builds up from our own
            # snapshots. The current sheet can still be the one in force for an entry a few days
            # old (e.g. Friday's sheet on Monday morning); the lookup checks its date anyway.
            recent = self.now().date() - day <= BANK_RATE_MAX_AGE
            if recent and self._due(source, SNAPSHOT_REFETCH_AFTER):
                fetch = getattr(self.sources, SNAPSHOT_SOURCES[source])
                self._fetch(bank_id, source, lambda: [fetch()])

    def _due(self, source: str, after: timedelta) -> bool:
        status = self.fetch_status(source)
        if status is None:
            return True
        wait = after if status["ok"] else RETRY_FAILED_AFTER
        return self.now() - datetime.fromisoformat(status["fetched_at"]) > wait

    def warm_up(self) -> None:
        """Fetch today's rate for every automatic bank (run in the background at startup)."""
        today = self.now().date()
        banks = self.conn.execute("SELECT id, rate_source FROM banks").fetchall()
        # Quick single-sheet sources first; SBI can be slow when it falls back to raw GitHub.
        banks.sort(key=lambda row: row["rate_source"] == "sbi")
        for row in banks:
            if row["rate_source"] in AUTO_SOURCES:
                self._refresh_bank(row["id"], today)

    def _fetch(self, bank_id: str, source: str, fetch) -> None:
        try:
            quotes: list[BankQuote] = fetch()
            ok, message = True, None
        except Exception as exc:
            log.warning("%s rate fetch failed: %s", source, exc)
            quotes, ok, message = [], False, str(exc)[:200]
        with self.conn:
            self.conn.executemany(
                "INSERT INTO bank_rates "
                "(bank_id, rate_date, published_at, tt_buy, tt_sell, origin) "
                "VALUES (?, ?, ?, ?, ?, 'auto') "
                "ON CONFLICT (bank_id, rate_date) DO UPDATE SET "
                "published_at = excluded.published_at, tt_buy = excluded.tt_buy, "
                "tt_sell = excluded.tt_sell WHERE bank_rates.origin = 'auto'",
                [
                    (
                        bank_id,
                        quote.rate_date.isoformat(),
                        quote.published_at.isoformat(),
                        f"{quote.tt_buy:f}",
                        f"{quote.tt_sell:f}",
                    )
                    for quote in quotes
                ],
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO fetch_log (source, fetched_at, ok, message) "
                "VALUES (?, ?, ?, ?)",
                (source, self.now().isoformat(), int(ok), message),
            )


def _yahoo_benchmark(bar: Bar, interval: str, seconds: int, at_ts: int) -> Benchmark:
    return Benchmark(
        rate=bar.close,
        source="yahoo",
        interval=interval,
        bar_at=datetime.fromtimestamp(bar.ts, IST),
        market_stale=is_quote_stale(at_ts, bar.ts, seconds),
    )


def _day_ts(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _bank_rate(row: sqlite3.Row) -> BankRate:
    return BankRate(
        bank_id=row["bank_id"],
        rate_date=date.fromisoformat(row["rate_date"]),
        published_at=datetime.fromisoformat(row["published_at"]),
        tt_buy=D(row["tt_buy"]),
        tt_sell=D(row["tt_sell"]),
        origin=row["origin"],
    )
