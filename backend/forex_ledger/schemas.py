from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from .config import IST

# Amounts are money (paise / cents); rates have at most 4 decimals. Anything more precise is
# rejected rather than silently rounded, so what's stored is exactly what the user sees.
Amount = Annotated[Decimal, Field(gt=0, decimal_places=2, max_digits=14)]
OptionalAmount = Annotated[Decimal, Field(ge=0, decimal_places=2, max_digits=14)]
Rate = Annotated[Decimal, Field(gt=0, decimal_places=4, max_digits=10)]


class ForexEntryIn(BaseModel):
    kind: Literal["deposit", "withdrawal"]
    occurred_at: datetime
    platform_id: str
    bank_id: str | None = None
    inr_amount: Amount
    usd_amount: Amount
    charges_inr: OptionalAmount = Decimal(0)
    tcs_inr: OptionalAmount = Decimal(0)
    market_rate_override: Rate | None = None
    bank_rate_override: Rate | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator("occurred_at")
    @classmethod
    def assume_ist(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=IST)


class SettingsIn(BaseModel):
    default_platform: str | None = None
    default_bank: str | None = None
    backup_dir: str | None = None


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
