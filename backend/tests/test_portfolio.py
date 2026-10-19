"""The ledger replay engine, fees, scenarios and tax — hand-checked cases plus invariants."""

from datetime import date, datetime, timedelta
from decimal import Decimal as Dec

import pytest

from forex_ledger.calc.fees import FeeRule, TradeFees, estimate_fees
from forex_ledger.calc.portfolio import Event, LedgerError, replay
from forex_ledger.calc.scenario import (
    break_even_price,
    break_even_value,
    sell_everything,
    sell_shares,
    take_fifo,
)
from forex_ledger.calc.tax import (
    add_months,
    estimate_capital_gains_tax,
    is_long_term,
    month_end_before,
)
from forex_ledger.config import IST

T0 = datetime(2026, 1, 5, 11, 0, tzinfo=IST)
INDMONEY = FeeRule(brokerage_pct=Dec("0.25"), brokerage_max_usd=Dec(25), brokerage_gst_pct=Dec(18))


def at(days: int, minutes: int = 0) -> datetime:
    return T0 + timedelta(days=days, minutes=minutes)


def deposit(i, when, usd, inr, rate, platform="p"):
    return Event("deposit", when, i, platform, Dec(rate), usd=Dec(usd), inr=Dec(inr))


def withdrawal(i, when, usd, inr, rate, platform="p"):
    return Event("withdrawal", when, i, platform, Dec(rate), usd=Dec(usd), inr=Dec(inr))


def buy(i, when, ticker, qty, price, fees="0", rate="90", platform="p"):
    return Event("buy", when, i, platform, Dec(rate), ticker=ticker, quantity=Dec(qty),
                 price=Dec(price), fees=Dec(fees))  # fmt: skip


def sell(i, when, ticker, qty, price, fees="0", rate="90", platform="p"):
    return Event("sell", when, i, platform, Dec(rate), ticker=ticker, quantity=Dec(qty),
                 price=Dec(price), fees=Dec(fees))  # fmt: skip


# ---- hand-checked scenarios -----------------------------------------------------------------


def test_currency_only_round_trip_matches_the_97_98_example():
    ledger = replay([
        deposit(1, at(0), "1000", "97000", "96"),
        withdrawal(2, at(5), "1000", "98000", "99"),
    ])  # fmt: skip
    w = ledger.withdrawals[0]
    assert (w.currency_inr, w.entry_cost_inr, w.exit_cost_inr, w.net_inr) == (
        3000,
        1000,
        1000,
        1000,
    )


def test_stock_round_trip_splits_into_stock_currency_and_costs():
    # ₹97,000 → $1,000 at mid 96; buy $1,000 of stock; sell for $1,100 at 98; bring $1,100 home at
    # mid 99, receiving ₹1,07,800.
    ledger = replay([
        deposit(1, at(0), "1000", "97000", "96"),
        buy(2, at(1), "ABC", "10", "100", rate="96"),
        sell(3, at(30), "ABC", "10", "110", rate="98"),
        withdrawal(4, at(31), "1100", "107800", "99"),
    ])  # fmt: skip
    sale, w = ledger.sales[0], ledger.withdrawals[0]
    assert (sale.gain_usd, sale.gain_inr) == (100, 9800)
    # $1,000 moved 96 → 99 (+3,000) and the $100 gain moved 98 → 99 (+100).
    assert w.currency_inr == 3100
    assert (w.entry_cost_inr, w.exit_cost_inr, w.net_inr) == (1000, 1100, 1000)
    assert sale.gain_inr + w.net_inr == Dec(107800) - Dec(97000)
    state = ledger.platforms["p"]
    assert (state.pool.usd, state.pool.inr, state.pool.mkt) == (0, 0, 0)


def test_spec_sell_calculator_example():
    """SPEC §6.3: 10 shares bought at $100 with $2.95 fees using dollars that cost ₹97 (mid 96);
    sell all at $110 at mid 98.50 with a 1% exit markup and INDmoney fees."""
    ledger = replay([
        deposit(1, at(0), "1002.95", "97286.15", "96"),
        buy(2, at(1), "ABC", "10", "100", fees="2.95", rate="96"),
    ])  # fmt: skip
    lots = ledger.platforms["p"].lots["ABC"]
    fees = estimate_fees(INDMONEY, "sell", Dec(10), Dec(110))
    assert fees == TradeFees(Dec("2.75"), Dec("0.50"), Dec(0))  # 0.495 rounds half-up
    slices = take_fifo(lots, Dec(10))
    e = sell_shares(slices, Dec(10), Dec(110), fees, Dec("98.50"), Dec(1), with_gst=True)

    assert e.usd_out == Dec("1096.75")
    assert e.conversion_gst_inr == Dec("187.2268875")  # 180 + 0.09% × (1,08,029.875 − 1,00,000)
    assert e.net_inr == Dec("9476.1993625")
    parts = (
        e.stock_gain_inr - e.brokerage_inr + e.currency_inr - e.entry_costs_inr
        - e.exit_markup_inr - e.conversion_gst_inr
    )  # fmt: skip
    assert parts == e.net_inr
    assert (e.stock_gain_inr, e.brokerage_inr) == (Dec(9850), Dec("610.700"))
    assert (e.currency_inr, e.entry_costs_inr) == (Dec("2507.375"), Dec("1002.95"))
    assert e.keep_in_usd == Dec("93.80")
    assert lots[0].qty == 10  # the scenario never touches the ledger

    be = break_even_price(slices, Dec(10), INDMONEY, Dec("98.50"), Dec(1), True)
    assert Dec("100.20") < be < Dec("100.30")

    def net_at(price):
        f = estimate_fees(INDMONEY, "sell", Dec(10), price)
        return sell_shares(slices, Dec(10), price, f, Dec("98.50"), Dec(1), True).net_inr

    assert net_at(be) >= 0 > net_at(be - Dec("0.01"))


def test_fifo_consumes_oldest_lots_and_keeps_totals_exact():
    ledger = replay([
        deposit(1, at(0), "3000", "270000", "89"),
        buy(2, at(1), "ABC", "10", "100", fees="3"),
        buy(3, at(2), "ABC", "10", "120", fees="3"),
        sell(4, at(3), "ABC", "15", "130"),
    ])  # fmt: skip
    sale = ledger.sales[0]
    assert [s.trade_id for s in sale.slices] == [2, 3]
    assert [s.qty for s in sale.slices] == [10, 5]
    remaining = ledger.platforms["p"].lots["ABC"]
    assert len(remaining) == 1 and remaining[0].qty == 5
    assert sale.slices[1].gross + remaining[0].gross == Dec(1200)
    assert sale.slices[1].fees + remaining[0].fees == Dec(3)


def test_split_multiplies_shares_and_keeps_cost():
    ledger = replay([
        deposit(1, at(0), "1000", "90000", "89"),
        buy(2, at(1), "ABC", "10", "100"),
        Event("split", at(2), 3, "p", ticker="ABC", split_from=1, split_to=10),
        sell(4, at(3), "ABC", "100", "11"),
    ])  # fmt: skip
    sale = ledger.sales[0]
    assert sale.cost_usd == 1000 and sale.gain_usd == 100
    assert sale.slices[0].acquired_at == at(1)


def test_merger_moves_all_cost_and_dates_to_new_ticker():
    ledger = replay([
        deposit(1, at(0), "2000", "180000", "89"),
        buy(2, at(1), "OLD", "10", "100", fees="1"),
        buy(3, at(2), "OLD", "10", "90", fees="1"),
        Event("adjustment", at(3), 4, "p", Dec(90), ticker="OLD", to_ticker="NEW",
              quantity=Dec(5), cost_fraction=Dec(1), cancel_source=True),
    ])  # fmt: skip
    state = ledger.platforms["p"]
    assert "OLD" not in state.lots
    new = state.lots["NEW"]
    assert sum(lot.qty for lot in new) == 5
    assert [lot.acquired_at for lot in new] == [at(1), at(2)]
    assert sum(lot.usd_cost for lot in new) == Dec(1902)
    assert sum(lot.inr for lot in new) + state.pool.inr == Dec(180000)


def test_spin_off_moves_part_of_the_cost():
    ledger = replay([
        deposit(1, at(0), "1000", "90000", "89"),
        buy(2, at(1), "PAR", "10", "100"),
        Event("adjustment", at(2), 3, "p", Dec(90), ticker="PAR", to_ticker="KID",
              quantity=Dec(3), cost_fraction=Dec("0.2")),
    ])  # fmt: skip
    lots = ledger.platforms["p"].lots
    assert lots["PAR"][0].qty == 10 and lots["PAR"][0].gross == 800
    assert lots["KID"][0].qty == 3 and lots["KID"][0].gross == 200
    assert lots["PAR"][0].inr + lots["KID"][0].inr == Dec(90000)


def test_cancelled_shares_must_move_all_cost():
    with pytest.raises(LedgerError):
        replay([
            deposit(1, at(0), "1000", "90000", "89"),
            buy(2, at(1), "PAR", "10", "100"),
            Event("adjustment", at(2), 3, "p", Dec(90), ticker="PAR", to_ticker="KID",
                  quantity=Dec(3), cost_fraction=Dec("0.5"), cancel_source=True),
        ])  # fmt: skip


def test_bonus_shares_and_write_offs():
    ledger = replay([
        deposit(1, at(0), "1000", "90000", "89"),
        buy(2, at(1), "ABC", "10", "100"),
        Event("adjustment", at(2), 3, "p", Dec(90), ticker="ABC", quantity=Dec(10)),
        Event("adjustment", at(3), 4, "p", Dec(91), ticker="ABC", quantity=Dec(-20)),
    ])  # fmt: skip
    write_off = ledger.sales[0]
    assert write_off.gross == 0 and write_off.cost_usd == 1000
    assert write_off.stock_gain_inr == Dec(-91000)  # $1,000 lost, at today's 91
    assert write_off.currency_inr == Dec(2000)  # 89 → 91 on those $1,000
    assert write_off.entry_cost_inr == Dec(1000)  # ₹90,000 paid vs ₹89,000 at mid
    assert write_off.gain_inr == Dec(-90000)  # exactly the rupees paid
    pool = ledger.platforms["p"].pool
    assert (pool.usd, pool.inr, pool.mkt) == (0, 0, 0)  # nothing orphaned


def test_impossible_ledgers_are_rejected():
    with pytest.raises(LedgerError, match="Only 5 shares"):
        replay([buy(1, at(0), "ABC", "5", "10"), sell(2, at(1), "ABC", "6", "10")])
    with pytest.raises(LedgerError):
        replay([Event("split", at(0), 1, "p", ticker="ABC", split_from=1, split_to=2)])


def test_spending_beyond_recorded_deposits_is_valued_at_market_and_flagged():
    ledger = replay([buy(1, at(0), "ABC", "1", "100", rate="95")])
    lot = ledger.platforms["p"].lots["ABC"][0]
    assert (lot.inr, lot.mkt) == (9500, 9500)
    assert ledger.uncovered[0][1] == 100


def test_same_time_events_put_money_in_first():
    ledger = replay([
        buy(1, at(0), "ABC", "10", "100"),  # lower id than the deposit, same time
        deposit(2, at(0), "1000", "90000", "89"),
    ])  # fmt: skip
    assert ledger.uncovered == []


def test_platforms_have_separate_dollar_pools():
    ledger = replay([
        deposit(1, at(0), "1000", "90000", "89", platform="a"),
        buy(2, at(1), "ABC", "1", "100", rate="95", platform="b"),
    ])  # fmt: skip
    assert ledger.platforms["a"].pool.usd == 1000
    assert ledger.uncovered and ledger.uncovered[0][0].platform == "b"


def test_dividends_join_the_pool_at_market():
    ledger = replay([
        Event("dividend", at(0), 1, "p", Dec("88"), usd=Dec("2.61"), ticker="VOO",
              gross_usd=Dec("3.48"), withheld_usd=Dec("0.87")),
    ])  # fmt: skip
    assert ledger.dividends[0].income_inr == Dec("229.68")
    assert ledger.platforms["p"].pool.inr == Dec("229.68")


def test_sell_everything_includes_cash_and_splits_exactly():
    ledger = replay([
        deposit(1, at(0), "1500", "135000", "89"),
        buy(2, at(1), "ABC", "5", "100", fees="1.50"),
        buy(3, at(1), "XYZ", "2", "200", fees="1.20"),
    ])  # fmt: skip
    state = ledger.platforms["p"]
    e = sell_everything(
        state, {"ABC": Dec("110"), "XYZ": Dec("190")}, INDMONEY, Dec("92"), Dec("0.8"), True
    )
    assert e.cash_usd == Dec("597.30")
    assert e.inr_cost == Dec(135000)
    parts = (
        e.stock_gain_inr - e.brokerage_inr + e.currency_inr - e.entry_costs_inr
        - e.exit_markup_inr - e.conversion_gst_inr
    )  # fmt: skip
    assert parts == e.net_inr


# ---- fees -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "qty", "price", "expected"),
    [
        (INDMONEY, "5", "236.50", ("2.96", "0.53")),  # 2.95625 → 2.96; 0.5328 → 0.53
        (INDMONEY, "100", "500", ("25.00", "4.50")),  # 0.25% = $125, capped at $25
        (FeeRule(brokerage_per_share_usd=Dec("0.0035"), brokerage_min_usd=Dec("0.35")),
         "10", "50", ("0.35", "0.00")),  # minimum applies
        (FeeRule(brokerage_per_share_usd=Dec("0.0035"), brokerage_min_usd=Dec("0.35")),
         "1000", "50", ("3.50", "0.00")),
        (FeeRule(), "10", "50", ("0.00", "0.00")),
    ],
)  # fmt: skip
def test_fee_rules(rule, qty, price, expected):
    fees = estimate_fees(rule, "buy", Dec(qty), Dec(price))
    assert (str(fees.brokerage_usd), str(fees.brokerage_gst_usd)) == expected
    assert fees.other_fees_usd == 0


def test_other_fees_apply_to_sells_only():
    rule = FeeRule(sell_fee_usd=Dec("0.03"))
    assert estimate_fees(rule, "buy", Dec(1), Dec(10)).other_fees_usd == 0
    assert estimate_fees(rule, "sell", Dec(1), Dec(10)).other_fees_usd == Dec("0.03")


# ---- break-even -----------------------------------------------------------------------------


def test_break_even_value_inverts_the_conversion():
    value = break_even_value(Dec(100000), Dec(95), Dec(1), with_gst=True)
    received = value * 95 * Dec("0.99") - (Dec(180) + (value * 95 - Dec(100000)) * Dec("0.0009"))
    assert received >= 100000
    below = value - Dec("0.01")
    gst = Dec(180) + (below * 95 - Dec(100000)) * Dec("0.0009")
    assert below * 95 * Dec("0.99") - gst < 100000


# ---- tax ------------------------------------------------------------------------------------


def test_month_end_before_and_month_arithmetic():
    assert month_end_before(datetime(2026, 3, 1, 0, 30, tzinfo=IST)) == date(2026, 2, 28)
    assert month_end_before(datetime(2024, 3, 15, tzinfo=IST)) == date(2024, 2, 29)
    assert month_end_before(datetime(2026, 1, 10, tzinfo=IST)) == date(2025, 12, 31)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2024, 2, 29), 24) == date(2026, 2, 28)


def test_long_term_means_more_than_24_months():
    bought = datetime(2024, 1, 15, 21, 0, tzinfo=IST)
    assert not is_long_term(bought, datetime(2026, 1, 15, 22, 0, tzinfo=IST))
    assert is_long_term(bought, datetime(2026, 1, 16, 9, 0, tzinfo=IST))


def _lot(acquired, qty, gross, fees="0"):
    ledger = replay([buy(1, acquired, "ABC", qty, str(Dec(gross) / Dec(qty)), fees=fees)])
    return ledger.platforms["p"].lots["ABC"][0]


def test_tax_converts_each_side_at_its_own_month_end_rate():
    rates = {date(2025, 10, 31): Dec("88.20"), date(2026, 9, 30): Dec("95.42")}
    lot = _lot(datetime(2025, 11, 6, 21, tzinfo=IST), "5", "976", fees="2.88")
    est = estimate_capital_gains_tax(
        (lot,), datetime(2026, 10, 19, 10, tzinfo=IST), Dec(910), Dec("2.69"), Dec(30), rates.get
    )
    assert est.lots[0].gain_inr == Dec("907.31") * Dec("95.42") - Dec("978.88") * Dec("88.20")
    assert est.short_term_inr == Dec("238.3042")
    assert est.tax_inr == Dec("238.3042") * Dec("0.30") * Dec("1.04")
    assert est.complete


def test_short_term_loss_offsets_long_term_gain_but_not_vice_versa():
    rate = {d: Dec(1) for d in (date(2023, 12, 31), date(2026, 5, 31), date(2026, 9, 30))}.get
    old = _lot(datetime(2024, 1, 10, tzinfo=IST), "1", "100")  # long-term at a 2026-10 sale
    new = _lot(datetime(2026, 6, 10, tzinfo=IST), "1", "300")  # short-term
    sold = datetime(2026, 10, 19, tzinfo=IST)
    est = estimate_capital_gains_tax((old, new), sold, Dec(400), Dec(0), Dec(30), rate)
    # sale value 200 each: LT +100, ST −100 → ST loss absorbs LT gain → no tax
    assert (est.long_term_inr, est.short_term_inr, est.tax_inr) == (100, -100, 0)

    est = estimate_capital_gains_tax((old, new), sold, Dec(200), Dec(0), Dec(30), rate)
    # sale value 100 each: LT 0, ST −200 → nothing taxable
    assert est.tax_inr == 0
    big_new = _lot(datetime(2026, 6, 10, tzinfo=IST), "1", "100")
    est = estimate_capital_gains_tax(
        (_lot(datetime(2024, 1, 10, tzinfo=IST), "1", "500"), big_new), sold, Dec(600), Dec(0),
        Dec(30), rate,
    )  # fmt: skip
    # LT −200 can't reduce ST +200: tax = 200 × 30% × 1.04
    assert est.tax_inr == Dec(200) * Dec("0.30") * Dec("1.04")


def test_missing_sbi_rate_marks_estimate_incomplete():
    lot = _lot(datetime(2019, 5, 1, tzinfo=IST), "1", "100")
    est = estimate_capital_gains_tax(
        (lot,), datetime(2026, 10, 19, tzinfo=IST), Dec(200), Dec(0), Dec(30),
        {date(2026, 9, 30): Dec(95)}.get,
    )  # fmt: skip
    assert not est.complete and est.lots[0].gain_inr is None
