from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forex_ledger.app import create_app
from forex_ledger.config import IST
from forex_ledger.rates.parsers import BankQuote, Bar

FIXTURES = Path(__file__).parent / "fixtures"


class FakeSources:
    """Stands in for the network. Yahoo returns a flat 96.00 for every minute requested."""

    def __init__(self, rate="96.00"):
        self.rate = Decimal(rate)
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def _call(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} down")

    def yahoo_bars(self, interval, start, end):
        self._call("yahoo")
        step = {"1m": 60, "5m": 300, "1h": 3600}[interval]
        return [Bar(ts, self.rate) for ts in range(start - start % step, end, step)]

    def frankfurter_daily(self, day: date):
        self._call("frankfurter")
        return day, self.rate

    def open_er_latest(self):
        self._call("open_er")
        return datetime.now(IST), self.rate

    def sbi_quotes(self):
        self._call("sbi")
        today = datetime.now(IST).replace(hour=9, minute=0, second=0, microsecond=0)
        return [BankQuote(today.date(), today, Decimal("95.50"), Decimal("96.50"))]

    def icici_quote(self):
        self._call("icici")
        now = datetime.now(IST).replace(second=0, microsecond=0)
        return BankQuote(
            now.date(), now.replace(hour=0, minute=0), Decimal("94.6"), Decimal("97.4")
        )

    def hdfc_quote(self):
        self._call("hdfc")
        raise RuntimeError("HDFC down")


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text()


@pytest.fixture
def sources():
    return FakeSources()


@pytest.fixture
def client(tmp_path, monkeypatch, sources):
    monkeypatch.setenv("FOREX_LEDGER_DATA_DIR", str(tmp_path))
    with TestClient(create_app(sources, warm_up=False)) as test_client:
        yield test_client
