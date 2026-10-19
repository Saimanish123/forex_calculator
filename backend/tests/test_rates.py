import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from forex_ledger import db
from forex_ledger.config import IST
from forex_ledger.rates import parsers
from forex_ledger.rates.parsers import BankQuote, Bar
from forex_ledger.rates.service import RateService, RateUnavailable, is_quote_stale, pick_bar

from .conftest import FakeSources


def test_parse_yahoo_skips_empty_bars(fixture_text):
    bars = parsers.parse_yahoo_chart(json.loads(fixture_text("yahoo_1m.json")))
    assert bars and all(isinstance(bar.close, Decimal) for bar in bars)
    assert bars[0].ts == 1792134000
    assert bars[0].close == Decimal("95.0275")  # float noise rounded to 4 dp
    assert parsers.parse_yahoo_chart({"chart": {"result": None}}) == []


def test_parse_sbi_csv(fixture_text):
    quotes = parsers.parse_sbi_csv(fixture_text("sbi_snippet.csv"))
    assert [q.rate_date for q in quotes][0] == date(2026, 10, 14)  # zero-rate row skipped
    last = quotes[-1]
    assert (last.tt_buy, last.tt_sell) == (Decimal("94.45"), Decimal("95.3"))
    assert last.published_at == datetime(2026, 10, 17, 9, 37, tzinfo=IST)


def test_parse_icici_html(fixture_text):
    quote = parsers.parse_icici_html(fixture_text("icici_snippet.html"))
    assert (quote.tt_buy, quote.tt_sell) == (Decimal("93.34"), Decimal("96.3"))
    assert quote.published_at == datetime(2026, 10, 16, 14, 41, 23, tzinfo=IST)


def test_parse_hdfc_text(fixture_text):
    quote = parsers.parse_hdfc_text(fixture_text("hdfc_snippet.txt"))
    assert (quote.tt_buy, quote.tt_sell) == (Decimal("93.27"), Decimal("96.67"))
    assert quote.published_at == datetime(2026, 10, 16, 12, 22, 26, tzinfo=IST)


def test_parsers_fail_loudly_on_unknown_layout():
    with pytest.raises(ValueError):
        parsers.parse_icici_html("<html>redesigned</html>")
    with pytest.raises(ValueError):
        parsers.parse_hdfc_text("something else")


def test_pick_bar_and_market_stale():
    bars = [Bar(0, Decimal("1")), Bar(60, Decimal("2")), Bar(120, Decimal("3"))]
    assert pick_bar(bars, 90).close == Decimal("2")
    assert pick_bar(bars, -1) is None
    assert not is_quote_stale(150, 120, 60)
    assert is_quote_stale(120 + 2 * 3600, 120, 60)  # weekend-style gap


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREX_LEDGER_DATA_DIR", str(tmp_path))
    conn = db.connect()
    db.migrate(conn)
    sources = FakeSources()
    yield RateService(conn, sources), sources
    conn.close()


def test_benchmark_uses_one_minute_bars_and_caches(service):
    rates, sources = service
    at = datetime.now(IST) - timedelta(hours=2)
    first = rates.benchmark(at)
    assert (first.source, first.interval, first.rate) == ("yahoo", "1m", Decimal("96.00"))
    assert not first.market_stale
    rates.benchmark(at)
    assert sources.calls.count("yahoo") == 1  # second lookup served from cache


def test_benchmark_interval_depends_on_age(service):
    rates, _ = service
    now = datetime.now(IST)
    assert rates.benchmark(now - timedelta(days=45)).interval == "5m"
    assert rates.benchmark(now - timedelta(days=300)).interval == "1h"
    old = rates.benchmark(now - timedelta(days=1000))
    assert (old.source, old.interval) == ("frankfurter", "1d")


def test_benchmark_falls_back_when_yahoo_is_down(service):
    rates, sources = service
    sources.fail.add("yahoo")
    result = rates.benchmark(datetime.now(IST) - timedelta(days=2))
    assert result.source == "frankfurter"
    sources.fail.add("frankfurter")
    with pytest.raises(RateUnavailable):
        rates.benchmark(datetime.now(IST) - timedelta(days=3))


def test_benchmark_rejects_future(service):
    rates, _ = service
    with pytest.raises(ValueError):
        rates.benchmark(datetime.now(IST) + timedelta(hours=1))


def test_bank_rates_respect_publish_time_and_failures(service):
    rates, sources = service
    today_9am = datetime.now(IST).replace(hour=9, minute=0, second=0, microsecond=0)
    assert rates.bank_rate("sbi", today_9am + timedelta(minutes=5)).tt_sell == Decimal("96.50")
    assert rates.bank_rate("sbi", today_9am - timedelta(minutes=5)) is None  # not yet published
    assert rates.bank_rate("hdfc", today_9am) is None  # source down → no comparison
    assert rates.fetch_status("hdfc")["ok"] == 0
    rates.bank_rate("hdfc", today_9am)
    assert sources.calls.count("hdfc") == 1  # failures are throttled too


def test_manual_bank_rate(service):
    rates, _ = service
    day = date(2026, 10, 1)
    rates.save_manual_bank_rate("axis", day, Decimal("94"), Decimal("97"))
    quote = rates.bank_rate("axis", datetime(2026, 10, 1, 15, 0, tzinfo=IST))
    assert (quote.tt_sell, quote.origin) == (Decimal("97"), "manual")


# ---- boundaries and caching, with a controllable clock --------------------------------------

FRI_1430 = datetime(2026, 10, 16, 14, 30, tzinfo=IST)


class MinuteSources(FakeSources):
    """Each minute has a distinct close (96 + minutes since midnight UTC / 10000); nothing trades
    from Friday 23:00 UTC until Monday 00:00 UTC, like the real USD/INR market."""

    def yahoo_bars(self, interval, start, end):
        self._call("yahoo")
        step = {"1m": 60, "5m": 300, "1h": 3600}[interval]
        bars = []
        for ts in range(start - start % step, end, step):
            moment = datetime.fromtimestamp(ts, UTC)
            if (
                moment.weekday() == 5
                or moment.weekday() == 6
                or (moment.weekday() == 4 and moment.hour >= 23)
            ):
                continue
            bars.append(Bar(ts, Decimal(96) + Decimal(ts % 86400 // 60) / 10000))
        return bars


def make_service(now, sources=None):
    conn = db.connect()
    db.migrate(conn)
    return RateService(conn, sources or MinuteSources(), now=lambda: now), conn


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREX_LEDGER_DATA_DIR", str(tmp_path))


def expected_close(at):
    minute_ts = int(at.timestamp()) // 60 * 60
    return Decimal(96) + Decimal(minute_ts % 86400 // 60) / 10000


def test_benchmark_uses_the_bar_containing_the_exact_second(data_dir):
    rates, conn = make_service(FRI_1430 + timedelta(hours=1))
    end_of_minute = FRI_1430.replace(second=59)
    next_minute = FRI_1430 + timedelta(minutes=1)
    assert rates.benchmark(end_of_minute).rate == expected_close(FRI_1430)
    assert rates.benchmark(next_minute).rate == expected_close(next_minute)
    assert rates.benchmark(next_minute).bar_at == next_minute
    conn.close()


def test_benchmark_flags_weekend_and_uses_last_close(data_dir):
    saturday = datetime(2026, 10, 17, 11, 0, tzinfo=IST)
    rates, conn = make_service(saturday + timedelta(hours=1))
    result = rates.benchmark(saturday)
    last_friday_bar = datetime(2026, 10, 16, 22, 59, tzinfo=UTC)
    assert result.market_stale
    assert result.bar_at == last_friday_bar.astimezone(IST)
    assert result.rate == expected_close(last_friday_bar)
    conn.close()


def test_editing_an_old_entry_keeps_its_one_minute_precision(data_dir):
    sources = MinuteSources()
    rates, conn = make_service(FRI_1430 + timedelta(days=1), sources)
    assert rates.benchmark(FRI_1430).interval == "1m"

    later = RateService(conn, sources, now=lambda: FRI_1430 + timedelta(days=40))
    again = later.benchmark(FRI_1430)
    assert (again.interval, again.rate) == ("1m", expected_close(FRI_1430))
    assert sources.calls.count("yahoo") == 1  # served from cache, no 5-minute refetch
    conn.close()


def test_unfinished_bar_is_used_but_not_cached(data_dir):
    now = FRI_1430 + timedelta(seconds=20)  # the 14:30 bar is still forming
    rates, conn = make_service(now)
    assert rates.benchmark(now).bar_at == FRI_1430
    cached = conn.execute(
        "SELECT COUNT(*) FROM fx_bars WHERE ts = ?", (int(FRI_1430.timestamp()),)
    ).fetchone()[0]
    previous = conn.execute(
        "SELECT COUNT(*) FROM fx_bars WHERE ts = ?", (int(FRI_1430.timestamp()) - 60,)
    ).fetchone()[0]
    assert (cached, previous) == (0, 1)
    conn.close()


class HolidaySources(FakeSources):
    def frankfurter_daily(self, day):
        self._call("frankfurter")
        return day - timedelta(days=1), Decimal("83.4600")  # ECB closed: previous day's fix


def test_daily_fallback_remembers_holiday_fix_date_in_cache(data_dir):
    sources = HolidaySources()
    sources.fail.add("yahoo")
    holiday = datetime(2023, 12, 25, 12, 0, tzinfo=IST)  # a Monday
    rates, conn = make_service(datetime(2026, 10, 19, 12, 0, tzinfo=IST), sources)
    first = rates.benchmark(holiday)
    second = rates.benchmark(holiday)
    assert first == second
    assert first.market_stale and first.bar_at.date() == holiday.date() - timedelta(days=1)
    assert sources.calls.count("frankfurter") == 1
    conn.close()


def test_bank_rate_publish_time_boundary_is_exact(data_dir):
    rates, conn = make_service(FRI_1430)
    rates.save_manual_bank_rate(
        "axis", FRI_1430.date() - timedelta(days=1), Decimal(94), Decimal(97)
    )
    with conn:
        conn.execute(
            "INSERT INTO bank_rates VALUES ('axis', ?, ?, '95', '98', 'auto')",
            (FRI_1430.date().isoformat(), FRI_1430.replace(hour=9, minute=15).isoformat()),
        )
    publish = FRI_1430.replace(hour=9, minute=15)
    before = rates.bank_rate("axis", publish - timedelta(microseconds=1))
    assert before.tt_sell == Decimal(97)  # yesterday's rate still in force
    assert rates.bank_rate("axis", publish).tt_sell == Decimal(98)
    assert rates.bank_rate("axis", publish + timedelta(microseconds=500)).tt_sell == Decimal(98)
    conn.close()


def test_bank_rates_older_than_four_days_are_not_used(data_dir):
    rates, conn = make_service(FRI_1430)
    rates.save_manual_bank_rate(
        "axis", FRI_1430.date() - timedelta(days=5), Decimal(94), Decimal(97)
    )
    assert rates.bank_rate("axis", FRI_1430) is None
    conn.close()


def test_failed_fetch_is_retried_after_ten_minutes(data_dir):
    sources = FakeSources()
    rates, conn = make_service(FRI_1430, sources)
    rates.bank_rate("hdfc", FRI_1430)
    soon = RateService(conn, sources, now=lambda: FRI_1430 + timedelta(minutes=9))
    soon.bank_rate("hdfc", FRI_1430)
    assert sources.calls.count("hdfc") == 1
    later = RateService(conn, sources, now=lambda: FRI_1430 + timedelta(minutes=11))
    later.bank_rate("hdfc", FRI_1430)
    assert sources.calls.count("hdfc") == 2
    conn.close()


class FridaySheetSources(FakeSources):
    """ICICI's current sheet was published on Friday afternoon (it's now Monday morning)."""

    def icici_quote(self):
        self._call("icici")
        published = datetime(2026, 10, 16, 14, 41, 23, tzinfo=IST)
        return BankQuote(published.date(), published, Decimal("93.34"), Decimal("96.30"))


def test_current_sheet_applies_to_recent_entries_only(data_dir):
    sources = FridaySheetSources()
    monday = datetime(2026, 10, 19, 6, 0, tzinfo=IST)
    rates, conn = make_service(monday, sources)

    friday_afternoon = datetime(2026, 10, 16, 16, 5, tzinfo=IST)
    assert rates.bank_rate("icici", friday_afternoon).tt_buy == Decimal("93.34")
    friday_morning = datetime(2026, 10, 16, 10, 0, tzinfo=IST)  # before that sheet was published
    assert rates.bank_rate("icici", friday_morning) is None

    old = RateService(conn, FridaySheetSources(), now=lambda: monday)
    assert old.bank_rate("icici", datetime(2026, 10, 2, 12, 0, tzinfo=IST)) is None
    assert old.sources.calls == []  # a 17-day-old entry never triggers a fetch
    conn.close()
