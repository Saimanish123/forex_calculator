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


# ---- conservation of money across whole random ledgers ----------------------------------------

from datetime import timedelta  # noqa: E402

from forex_ledger.calc.fees import FeeRule  # noqa: E402
from forex_ledger.calc.portfolio import Event, replay  # noqa: E402
from forex_ledger.calc.scenario import sell_everything  # noqa: E402

TOLERANCE = Decimal("1e-12")  # pro-rata splits divide at 28 significant digits
money = st.decimals(min_value=Decimal("1.00"), max_value=Decimal("5000.00"), places=2)
fx = st.decimals(min_value=Decimal("70.0000"), max_value=Decimal("110.0000"), places=4)
share_price = st.decimals(min_value=Decimal("1.0000"), max_value=Decimal("900.0000"), places=4)
fee = st.decimals(min_value=Decimal("0"), max_value=Decimal("5.00"), places=2)


def realized_total(ledger) -> Decimal:
    return (
        sum((s.gain_inr for s in ledger.sales), Decimal(0))
        + sum((d.income_inr for d in ledger.dividends), Decimal(0))
        + sum((w.net_inr for w in ledger.withdrawals), Decimal(0))
    )


@st.composite
def ledgers(draw):
    """A random but possible ledger over two platforms (never sells shares it doesn't hold and
    never withdraws more dollars than it has, so nothing is valued outside the books)."""
    events, t, n = [], datetime(2025, 1, 1, 10, tzinfo=IST), 0
    held = {}  # (platform, ticker) → qty
    cash = {"a": Decimal(0), "b": Decimal(0)}
    inr_in = inr_out = Decimal(0)
    for _ in range(draw(st.integers(3, 30))):
        t += timedelta(hours=draw(st.integers(1, 500)))
        n += 1
        platform = draw(st.sampled_from(["a", "b"]))
        rate = draw(fx)
        op = draw(st.sampled_from(["deposit", "deposit", "buy", "buy", "sell", "split",
                                   "dividend", "withdrawal", "bonus", "writeoff"]))  # fmt: skip
        if op == "deposit":
            usd = draw(money)
            cost = (usd * rate * draw(st.decimals(Decimal("1.000"), Decimal("1.030"), places=3))
                    ).quantize(Decimal("0.01"))  # fmt: skip
            events.append(Event("deposit", t, n, platform, rate, usd=usd, inr=cost))
            cash[platform] += usd
            inr_in += cost
        elif op == "buy" and cash[platform] > 2:
            ticker = draw(st.sampled_from(["AAA", "BBB"]))
            price = draw(share_price)
            fees = min(draw(fee), cash[platform] / 4).quantize(Decimal("0.01"))
            qty = ((cash[platform] - fees) / price * draw(st.decimals(Decimal("0.1"), Decimal("1"),
                   places=2))).quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")  # fmt: skip
            if qty <= 0:
                continue
            events.append(Event("buy", t, n, platform, rate, ticker=ticker, quantity=qty,
                                price=price, fees=fees))  # fmt: skip
            cash[platform] -= qty * price + fees
            held[(platform, ticker)] = held.get((platform, ticker), Decimal(0)) + qty
        elif op == "sell":
            owned = [(k, q) for k, q in held.items() if k[0] == platform and q > 0]
            if not owned:
                continue
            (p, ticker), qty_held = draw(st.sampled_from(owned))
            qty = (qty_held * draw(st.decimals(Decimal("0.1"), Decimal("1"), places=2))
                   ).quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")  # fmt: skip
            price = draw(share_price)
            fees = min(draw(fee), qty * price / 4).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
            if qty <= 0:
                continue
            events.append(Event("sell", t, n, platform, rate, ticker=ticker, quantity=qty,
                                price=price, fees=fees))  # fmt: skip
            cash[platform] += qty * price - fees
            held[(p, ticker)] -= qty
        elif op == "split":
            owned = [k for k, q in held.items() if k[0] == platform and q > 0]
            if not owned:
                continue
            p, ticker = draw(st.sampled_from(owned))
            ratio_to, ratio_from = draw(st.sampled_from([(2, 1), (10, 1), (3, 2), (1, 4)]))
            events.append(Event("split", t, n, platform, ticker=ticker, split_from=ratio_from,
                                split_to=ratio_to))  # fmt: skip
            held[(p, ticker)] = held[(p, ticker)] * ratio_to / ratio_from
        elif op in ("bonus", "writeoff"):
            owned = [k for k, q in held.items() if k[0] == platform and q > 0]
            if not owned:
                continue
            p, ticker = draw(st.sampled_from(owned))
            qty = (held[(p, ticker)] * draw(st.decimals(Decimal("0.1"), Decimal("1"), places=2))
                   ).quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")  # fmt: skip
            if qty <= 0:
                continue
            signed = qty if op == "bonus" else -qty
            events.append(Event("adjustment", t, n, platform, rate, ticker=ticker,
                                quantity=signed))  # fmt: skip
            held[(p, ticker)] += signed
        elif op == "dividend":
            usd = draw(st.decimals(Decimal("0.01"), Decimal("50.00"), places=2))
            events.append(Event("dividend", t, n, platform, rate, usd=usd, ticker="AAA",
                                gross_usd=usd, withheld_usd=Decimal(0)))  # fmt: skip
            cash[platform] += usd
        elif op == "withdrawal" and cash[platform] >= 1:
            usd = (cash[platform] * draw(st.decimals(Decimal("0.1"), Decimal("1"), places=2))
                   ).quantize(Decimal("0.01"), rounding="ROUND_DOWN")  # fmt: skip
            received = (usd * rate * draw(st.decimals(Decimal("0.970"), Decimal("1.000"),
                        places=3))).quantize(Decimal("0.01"))  # fmt: skip
            events.append(Event("withdrawal", t, n, platform, rate, usd=usd, inr=received))
            cash[platform] -= usd
            inr_out += received
    prices = {t: draw(share_price) for t in ("AAA", "BBB")}
    return events, inr_in, inr_out, prices, draw(fx), t


@settings(max_examples=300)
@given(case=ledgers())
def test_realized_plus_exit_equals_money_out_minus_money_in(case):
    events, inr_in, inr_out, prices, rate, last = case
    ledger = replay(events)
    assert ledger.uncovered == []

    # Hypothetical: sell everything at `prices` and convert all dollars now, per platform.
    exit_received = exit_net = Decimal(0)
    for state in ledger.platforms.values():
        e = sell_everything(state, prices, FeeRule(), rate, Decimal("0.7"), True)
        parts = (e.stock_gain_inr - e.brokerage_inr + e.currency_inr - e.entry_costs_inr
                 - e.exit_markup_inr - e.conversion_gst_inr)  # fmt: skip
        assert abs(parts - e.net_inr) < TOLERANCE
        exit_received += e.inr_received
        exit_net += e.net_inr
    assert abs(realized_total(ledger) + exit_net - (inr_out + exit_received - inr_in)) < TOLERANCE


@settings(max_examples=300)
@given(case=ledgers())
def test_full_liquidation_realizes_exactly_money_out_minus_money_in(case):
    events, inr_in, inr_out, prices, rate, last = case
    ledger = replay(events)
    # Actually sell every holding and withdraw every dollar, then compare with cash in vs out.
    extra, n, t = [], 10_000, last
    for platform, state in ledger.platforms.items():
        for ticker, lots in state.lots.items():
            qty = sum((lot.qty for lot in lots), Decimal(0))
            if qty > 0:
                n, t = n + 1, t + timedelta(minutes=1)
                extra.append(Event("sell", t, n, platform, rate, ticker=ticker, quantity=qty,
                                   price=prices[ticker]))  # fmt: skip
    final = replay(events + extra)
    for platform, state in final.platforms.items():
        if state.pool.usd > 0 or state.pool.inr != 0:
            n, t = n + 1, t + timedelta(minutes=1)
            received = (state.pool.usd * rate).quantize(Decimal("0.01"))
            extra.append(Event("withdrawal", t, n, platform, rate, usd=state.pool.usd,
                               inr=received))  # fmt: skip
            inr_out += received
    final = replay(events + extra)
    for state in final.platforms.values():
        assert state.pool.usd == 0 and abs(state.pool.inr) < TOLERANCE
        assert all(lot.qty == 0 for lots in state.lots.values() for lot in lots)
    assert abs(realized_total(final) - (inr_out - inr_in)) < TOLERANCE
