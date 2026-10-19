from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from .config import IST

# Anything more precise than these is rejected rather than silently rounded, so what's stored is
# exactly what the user sees. Money: paise / cents. Rates: 4 decimals. Shares: fractional shares
# to 8 decimals. Share prices and US fees: 4 decimals.
Amount = Annotated[Decimal, Field(gt=0, decimal_places=2, max_digits=14)]
OptionalAmount = Annotated[Decimal, Field(ge=0, decimal_places=2, max_digits=14)]
Rate = Annotated[Decimal, Field(gt=0, decimal_places=4, max_digits=10)]
Quantity = Annotated[Decimal, Field(gt=0, decimal_places=8, max_digits=20)]
SignedQuantity = Annotated[Decimal, Field(decimal_places=8, max_digits=20)]
Price = Annotated[Decimal, Field(gt=0, decimal_places=4, max_digits=14)]
Fee = Annotated[Decimal, Field(ge=0, decimal_places=4, max_digits=14)]
Percent = Annotated[Decimal, Field(ge=0, le=100, decimal_places=4)]


class _Timed(BaseModel):
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def assume_ist(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=IST)


class ForexEntryIn(_Timed):
    kind: Literal["deposit", "withdrawal", "dividend"]
    platform_id: str
    bank_id: str | None = None
    inr_amount: Amount | None = None
    usd_amount: Amount | None = None
    charges_inr: OptionalAmount = Decimal(0)
    tcs_inr: OptionalAmount = Decimal(0)
    market_rate_override: Rate | None = None
    bank_rate_override: Rate | None = None
    note: str | None = Field(default=None, max_length=500)
    ticker: str | None = Field(default=None, max_length=15)  # dividends
    gross_usd: Amount | None = None
    withheld_usd: OptionalAmount = Decimal(0)


class TradeIn(_Timed):
    kind: Literal["buy", "sell", "split", "adjustment"]
    platform_id: str
    ticker: str = Field(min_length=1, max_length=15)
    quantity: Quantity | None = None
    price_usd: Price | None = None
    brokerage_usd: Fee = Decimal(0)
    brokerage_gst_usd: Fee = Decimal(0)
    other_fees_usd: Fee = Decimal(0)
    split_from: int | None = Field(default=None, gt=0, le=1_000_000)
    split_to: int | None = Field(default=None, gt=0, le=1_000_000)
    qty_change: SignedQuantity | None = None
    to_ticker: str | None = Field(default=None, max_length=15)
    cost_fraction: Annotated[Decimal, Field(gt=0, le=1, decimal_places=8)] | None = None
    cancel_source: bool = False
    market_rate_override: Rate | None = None
    note: str | None = Field(default=None, max_length=500)


class FeeEstimateIn(BaseModel):
    platform_id: str
    side: Literal["buy", "sell"]
    quantity: Quantity
    price_usd: Price


class CalculatorIn(BaseModel):
    platform_id: str
    ticker: str = Field(min_length=1, max_length=15)
    quantity: Quantity
    price_usd: Price
    exit_markup_pct: Percent | None = None
    market_rate_override: Rate | None = None
    brokerage_usd: Fee | None = None  # actual sell fees, if known; else estimated
    brokerage_gst_usd: Fee | None = None
    other_fees_usd: Fee | None = None
    include_gst: bool | None = None
    include_tax: bool = True
    slab_pct: Percent | None = None

    @field_validator("ticker")
    @classmethod
    def upper(cls, value: str) -> str:
        return value.strip().upper()


class PriceIn(BaseModel):
    price_usd: Price


class SettingsIn(BaseModel):
    default_platform: str | None = None
    default_bank: str | None = None
    backup_dir: str | None = None
    tax_slab_pct: Percent | None = None
    estimate_conversion_gst: bool | None = None


class PlatformIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["gift_city", "us_broker", "bank_lrs"] = "us_broker"
    brokerage_pct: Decimal = Field(default=Decimal(0), ge=0)
    brokerage_min_usd: Decimal = Field(default=Decimal(0), ge=0)
    brokerage_max_usd: Decimal | None = Field(default=None, ge=0)
    brokerage_per_share_usd: Decimal = Field(default=Decimal(0), ge=0)
    brokerage_flat_usd: Decimal = Field(default=Decimal(0), ge=0)
    brokerage_gst_pct: Decimal = Field(default=Decimal(0), ge=0)
    sell_fee_usd: Decimal = Field(default=Decimal(0), ge=0)
    transfer_fee_inr: Decimal = Field(default=Decimal(0), ge=0)
    expected_markup_pct: Decimal = Field(default=Decimal(1), ge=0)


class BankIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class BankRateIn(BaseModel):
    bank_id: str
    rate_date: date
    tt_buy: Rate
    tt_sell: Rate


class DeleteAllIn(BaseModel):
    confirm: Literal["DELETE"]
