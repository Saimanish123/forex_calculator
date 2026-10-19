"""Invariants of the conversion math, checked over generated inputs."""

from datetime import datetime
from decimal import Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from forex_ledger import forex
from forex_ledger.calc.conversions import analyze_conversion
from forex_ledger.calc.gst import conversion_gst
from forex_ledger.config import IST

amounts = st.decimals(min_value=Decimal("1.00"), max_value=Decimal("50000000"), places=2)
usd_amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("500000"), places=2)
rates = st.decimals(min_value=Decimal("40.0000"), max_value=Decimal("200.0000"), places=4)
TINY = Decimal("1e-18")

settings.register_profile("default", max_examples=400, deadline=None)
settings.load_profile("default")


@given(inr=amounts, usd=usd_amounts, market=rates, charge_share=st.integers(0, 50))
def test_deposit_decomposes_exactly(inr, usd, market, charge_share):
    charges = (inr * charge_share / 1000).quantize(Decimal("0.01"))
    assume(charges < inr)
    r = analyze_conversion("deposit", inr, usd, market, charges_inr=charges)

    assert r.converted_inr == inr - charges
    assert r.market_inr == usd * market
    assert r.converted_inr == r.market_inr + r.markup_inr  # exact, no rounding
    assert r.hidden_cost_inr == r.markup_inr + charges
    assert (r.markup_inr > 0) == (r.rate_got > market)
    assert abs(r.rate_got * usd - r.converted_inr) < TINY * r.converted_inr
    assert abs(r.markup_pct - (r.rate_got / market - 1) * 100) < TINY * 1000


@given(inr=amounts, usd=usd_amounts, market=rates)
def test_withdrawal_decomposes_exactly(inr, usd, market):
    r = analyze_conversion("withdrawal", inr, usd, market)
    assert r.market_inr == r.converted_inr + r.markup_inr
    assert (r.markup_inr > 0) == (r.rate_got < market)
    assert abs(r.markup_pct - (1 - r.rate_got / market) * 100) < TINY * 1000


@given(inr=amounts, usd=usd_amounts, market=rates, k=st.integers(2, 1000))
def test_scaling_amounts_keeps_rates_and_percentages(inr, usd, market, k):
    base = analyze_conversion("deposit", inr, usd, market)
    scaled = analyze_conversion("deposit", inr * k, usd * k, market)
    assert abs(scaled.rate_got - base.rate_got) < TINY
    assert abs(scaled.markup_pct - base.markup_pct) < TINY
    assert scaled.markup_inr == base.markup_inr * k


@given(usd=usd_amounts, m_in=rates, m_out=rates, paid=amounts, received=amounts)
def test_round_trip_profit_splits_into_currency_move_and_markups(usd, m_in, m_out, paid, received):
    """₹ out − ₹ in = $ × (market move) − markup in − markup out. The 97/98 example, generalized."""
    deposit = analyze_conversion("deposit", paid, usd, m_in)
    withdrawal = analyze_conversion("withdrawal", received, usd, m_out)
    assert received - paid == usd * (m_out - m_in) - deposit.markup_inr - withdrawal.markup_inr


@given(inr=amounts, usd=usd_amounts, market=rates, bank=rates)
def test_bank_comparison_sign(inr, usd, market, bank):
    deposit = analyze_conversion("deposit", inr, usd, market, bank_rate=bank)
    assert deposit.vs_bank_inr == usd * bank - inr
    assert (deposit.vs_bank_inr > 0) == (usd * bank > inr)  # bank would have charged more
    withdrawal = analyze_conversion("withdrawal", inr, usd, market, bank_rate=bank)
    assert (withdrawal.vs_bank_inr > 0) == (inr > usd * bank)  # bank would have paid less


@given(inr=amounts, usd=usd_amounts, market=rates, charges=st.decimals(0, 5000, places=2))
def test_paise_rounding_is_consistent(inr, usd, market, charges):
    assume(charges < inr)
    exact = analyze_conversion("deposit", inr, usd, market, charges_inr=charges)
    money = forex.paise(exact)
    assert money["hidden_cost_inr"] == money["markup_inr"] + money["charges_inr"]
    for key in ("markup_inr", "market_inr", "converted_inr"):
        assert money[key].as_tuple().exponent == -2
        assert abs(money[key] - getattr(exact, key)) <= Decimal("0.005")


entry_strategy = st.builds(
    lambda kind, inr, usd, market, charges, bank: {
        "id": 1,
        "kind": kind,
        "occurred_at": datetime(2026, 10, 16, 14, 30, tzinfo=IST),
        "platform_id": "indmoney",
        "bank_id": "sbi" if bank else None,
        "inr_amount": inr,
        "usd_amount": usd,
        "charges_inr": charges if charges < inr else Decimal("0"),
        "tcs_inr": Decimal("0"),
        "note": None,
        "market_rate": market,
        "market_source": "yahoo",
        "market_interval": "1m",
        "market_bar_at": None,
        "market_stale": 0,
        "bank_rate": bank,
        "bank_rate_date": None,
        "bank_rate_manual": 0,
    },
    st.sampled_from(["deposit", "withdrawal"]),
    st.decimals(min_value=Decimal("1.00"), max_value=Decimal("2000000"), places=2),
    st.decimals(min_value=Decimal("0.01"), max_value=Decimal("20000"), places=2),
    rates,
    st.decimals(0, 500, places=2),
    st.one_of(st.none(), rates),
)


@given(entries=st.lists(entry_strategy, min_size=1, max_size=25))
def test_summary_totals_equal_sum_of_rows(entries):
    rows = [forex.serialize(e) for e in entries]
    total = forex.summary(entries)
    assert Decimal(total["hidden_cost_inr"]) == sum(
        Decimal(r["analysis"]["hidden_cost_inr"]) for r in rows
    )
    compared = [r for r in rows if r["analysis"]["vs_bank_inr"] is not None]
    if compared:
        assert Decimal(total["vs_bank_inr"]) == sum(
            Decimal(r["analysis"]["vs_bank_inr"]) for r in compared
        )
    for kind, side in (("deposit", "deposits"), ("withdrawal", "withdrawals")):
        mine = [r for r in rows if r["kind"] == kind]
        assert total[side]["count"] == len(mine)
        assert Decimal(total[side]["markup_inr"]) == sum(
            (Decimal(r["analysis"]["markup_inr"]) for r in mine), Decimal(0)
        )


@given(a=st.decimals(0, 50_000_000, places=2), b=st.decimals(0, 50_000_000, places=2))
def test_gst_is_monotonic_and_bounded(a, b):
    low, high = sorted((a, b))
    assert conversion_gst(low) <= conversion_gst(high)
    if high > 0:
        assert Decimal(45) <= conversion_gst(high) <= Decimal(10800)


def test_gst_is_continuous_at_slab_edges():
    step = Decimal("0.01")
    for edge in (Decimal(100000), Decimal(1000000)):
        assert abs(conversion_gst(edge + step) - conversion_gst(edge)) < Decimal("0.001")
