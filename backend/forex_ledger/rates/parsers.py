"""Pure parsers for each external rate source. One function per source, so a layout change at one
source only breaks that source."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ..config import IST
from ..money import D, q


@dataclass(frozen=True)
class Bar:
    ts: int  # bar start, unix seconds
    close: Decimal


@dataclass(frozen=True)
class BankQuote:
    rate_date: date
    published_at: datetime
    tt_buy: Decimal
    tt_sell: Decimal


def parse_yahoo_chart(payload: dict) -> list[Bar]:
    """Yahoo chart API (`INR=X`). Bars with no close are skipped."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result or "timestamp" not in result[0]:
        return []
    timestamps = result[0]["timestamp"]
    closes = result[0]["indicators"]["quote"][0]["close"]
    return [
        Bar(ts, q(D(close), 4))
        for ts, close in zip(timestamps, closes, strict=True)
        if close is not None and close > 0
    ]


def parse_yahoo_price(payload: dict) -> tuple[Decimal, datetime]:
    """Latest price of a US-listed stock or ETF from Yahoo's chart API."""
    chart = payload.get("chart") or {}
    result = chart.get("result") or []
    if not result:
        error = (chart.get("error") or {}).get("description") or "Ticker not found"
        raise LookupError(error)
    meta = result[0]["meta"]
    if meta.get("currency") != "USD":
        raise ValueError(f"{meta.get('symbol')} isn't priced in US dollars")
    price = meta.get("regularMarketPrice")
    if not price or price <= 0:
        raise LookupError("No price available")
    return q(D(price), 4), datetime.fromtimestamp(meta["regularMarketTime"], IST)


def parse_frankfurter(payload: dict) -> tuple[date, Decimal]:
    return date.fromisoformat(payload["date"]), D(payload["rates"]["INR"])


def parse_open_er(payload: dict) -> tuple[datetime, Decimal]:
    updated = datetime.fromtimestamp(payload["time_last_update_unix"], IST)
    return updated, D(payload["rates"]["INR"])


def parse_sbi_csv(text: str) -> list[BankQuote]:
    """sahilgupta/sbi-fx-ratekeeper USD CSV. Rows with zero TT rates (holidays) are skipped."""
    quotes = []
    for row in csv.DictReader(io.StringIO(text)):
        tt_buy, tt_sell = D(row["TT BUY"]), D(row["TT SELL"])
        if not tt_buy or not tt_sell:
            continue
        published = datetime.strptime(row["DATE"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        quotes.append(BankQuote(published.date(), published, tt_buy, tt_sell))
    return quotes


_ICICI_UPDATED = re.compile(
    r"Last Updated on:\s*(\d{2}-\d{2}-\d{4})\s*\|\s*(\d{1,2}:\d{2}:\d{2}\s*[AP]M)", re.I
)
_ICICI_USD_ROW = re.compile(r'data-value="United States Dollar \(USD\)"(.*?)</tr>', re.S)
_NUMBER = re.compile(r"<td[^>]*>\s*([\d.]+)\s*</td>")


_HDFC_UPDATED = re.compile(
    r"DATE:\s*(\d{2}/\d{2}/\d{4})\s*TIME:\s*(\d{1,2}:\d{2}:\d{2}\s*[AP]M)", re.I
)
_HDFC_USD_ROW = re.compile(r"United States Dollar USD((?:\s+(?:[\d.]+|-))+)")


def parse_hdfc_text(text: str) -> BankQuote:
    """Text of HDFC's treasury forex card-rate PDF. USD row columns: cash buy, cash sell,
    bills buy, bills sell, TT buy, TT sell, card cash-out, card load, DD."""
    updated = _HDFC_UPDATED.search(text)
    row = _HDFC_USD_ROW.search(text)
    if not updated or not row:
        raise ValueError("HDFC rate sheet layout not recognised")
    numbers = row.group(1).split()
    if len(numbers) < 6 or "-" in numbers[4:6]:
        raise ValueError("HDFC USD row not recognised")
    published = datetime.strptime(
        f"{updated.group(1)} {updated.group(2).upper()}", "%d/%m/%Y %I:%M:%S %p"
    ).replace(tzinfo=IST)
    return BankQuote(published.date(), published, D(numbers[4]), D(numbers[5]))


def parse_icici_html(html: str) -> BankQuote:
    """ICICI forex card-rate page. USD row columns: TT buy, bills buy, notes buy, card buy,
    TT sell, bills sell, notes sell, card sell, demand draft."""
    updated = _ICICI_UPDATED.search(html)
    row = _ICICI_USD_ROW.search(html)
    if not updated or not row:
        raise ValueError("ICICI page layout not recognised")
    numbers = _NUMBER.findall(row.group(1))
    if len(numbers) < 5:
        raise ValueError("ICICI USD row not recognised")
    published = datetime.strptime(
        f"{updated.group(1)} {updated.group(2).upper()}", "%d-%m-%Y %I:%M:%S %p"
    ).replace(tzinfo=IST)
    return BankQuote(published.date(), published, D(numbers[0]), D(numbers[4]))
