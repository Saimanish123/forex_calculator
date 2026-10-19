"""Network access to rate sources. Swapped for a fake in tests."""

import io
import logging
from datetime import date, datetime
from decimal import Decimal

import httpx
from pypdf import PdfReader

from ..config import IST
from . import parsers
from .parsers import BankQuote, Bar

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_URL = f"{YAHOO_CHART_URL}/INR=X"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{day}"
OPEN_ER_URL = "https://open.er-api.com/v6/latest/USD"
SBI_CSV_PATH = "csv_files/SBI_REFERENCE_RATES_USD.csv"
# The jsDelivr mirror answers in ~1s but may lag GitHub by up to 12h; raw GitHub is always current
# but can take 30s+ to respond, so it's only tried by the background warm-up.
SBI_CDN_URL = f"https://cdn.jsdelivr.net/gh/sahilgupta/sbi-fx-ratekeeper@main/{SBI_CSV_PATH}"
SBI_RAW_URL = f"https://raw.githubusercontent.com/sahilgupta/sbi-fx-ratekeeper/main/{SBI_CSV_PATH}"
log = logging.getLogger(__name__)

ICICI_URL = "https://www.icici.bank.in/corporate/global-markets/forex/forex-card-rate"
HDFC_PDF_URL = (
    "https://www.hdfc.bank.in/content/dam/hdfcbankpws/in/en/personal-banking/discover-products/"
    "interest-rates/hdfc-bank-treasury-forex-card-rates.pdf"
)


class RateSources:
    # Short by default: these calls happen while the user waits on the form. The startup warm-up
    # uses a separate instance with a long timeout.
    def __init__(self, timeout: float = 6, thorough: bool = False):
        self.thorough = thorough
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (forex-ledger)"},
        )

    def _get(self, url: str, **params) -> httpx.Response:
        response = self.client.get(url, params=params or None)
        response.raise_for_status()
        return response

    def yahoo_bars(self, interval: str, start: int, end: int) -> list[Bar]:
        payload = self._get(YAHOO_URL, interval=interval, period1=start, period2=end).json()
        return parsers.parse_yahoo_chart(payload)

    def stock_price(self, ticker: str) -> tuple[Decimal, datetime]:
        response = self.client.get(
            f"{YAHOO_CHART_URL}/{ticker}", params={"range": "1d", "interval": "1d"}
        )
        if response.status_code == 404:
            raise LookupError(f"Yahoo has no data for {ticker}")
        response.raise_for_status()
        return parsers.parse_yahoo_price(response.json())

    def frankfurter_daily(self, day: date) -> tuple[date, Decimal]:
        url = FRANKFURTER_URL.format(day=day.isoformat())
        return parsers.parse_frankfurter(self._get(url, base="USD", symbols="INR").json())

    def open_er_latest(self) -> tuple[datetime, Decimal]:
        return parsers.parse_open_er(self._get(OPEN_ER_URL).json())

    def sbi_quotes(self) -> list[BankQuote]:
        quotes = parsers.parse_sbi_csv(self._get(SBI_CDN_URL).text)
        if self.thorough and (not quotes or quotes[-1].rate_date < datetime.now(IST).date()):
            try:
                quotes = parsers.parse_sbi_csv(self._get(SBI_RAW_URL).text)
            except Exception as exc:
                log.warning("SBI raw GitHub fetch failed, keeping CDN copy: %s", exc)
        return quotes

    def icici_quote(self) -> BankQuote:
        return parsers.parse_icici_html(self._get(ICICI_URL).text)

    def hdfc_quote(self) -> BankQuote:
        pdf = PdfReader(io.BytesIO(self._get(HDFC_PDF_URL).content))
        return parsers.parse_hdfc_text("\n".join(page.extract_text() for page in pdf.pages))
