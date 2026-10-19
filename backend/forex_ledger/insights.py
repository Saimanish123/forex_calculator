"""Read-side views over the replayed ledger: holdings, the sell calculator, the dashboard, TCS.

Display rounding: each rupee part is rounded to the paisa once and every total shown is built
from those rounded parts, so what the user sees always adds up exactly.
"""

import sqlite3
from datetime import date, datetime
from decimal import Decimal

from . import forex, ledger
from .calc.fees import TradeFees, estimate_fees
from .calc.portfolio import Ledger, PlatformState
from .calc.scenario import (
    ExitBreakdown,
    break_even_price,
    break_even_value,
    sell_everything,
    sell_shares,
    take_fifo,
)
from .calc.tax import estimate_capital_gains_tax, month_end_before
from .config import IST
from .money import HUNDRED, ZERO, D, fmt, q
from .rates.service import Benchmark, RateService

TCS_THRESHOLD_INR = Decimal("1000000")


def settings(conn: sqlite3.Connection) -> dict:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}


def platform_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM platforms")}


def default_exit_markup(conn: sqlite3.Connection, platform_id: str) -> tuple[Decimal, str]:
    """Your average withdrawal markup on this platform, else the platform's expected markup."""
    entries = [
        e for e in forex.list_entries(conn, ("withdrawal",)) if e["platform_id"] == platform_id
    ]
    if entries:
        avg = forex.summary(entries)["withdrawals"]["avg_markup_pct"]
        if avg is not None:
            return max(D(avg), ZERO), "history"
    row = conn.execute(
        "SELECT expected_markup_pct FROM platforms WHERE id = ?", (platform_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"Unknown platform: {platform_id}")
    return D(row["expected_markup_pct"]), "platform"


def benchmark_json(benchmark: Benchmark | None) -> dict | None:
    if benchmark is None:
        return None
    return {
        "rate": fmt(benchmark.rate, 4),
        "source": benchmark.source,
        "interval": benchmark.interval,
        "bar_at": benchmark.bar_at.isoformat(),
        "stale": benchmark.market_stale,
    }


# ---- holdings ------------------------------------------------------------------------------


def holdings(conn: sqlite3.Connection, built: Ledger | None = None) -> list[dict]:
    built = built or ledger.build(conn)
    names, known = platform_names(conn), ledger.prices(conn)
    result = []
    for platform_id, state in sorted(built.platforms.items()):
        rows = []
        for ticker, lots in sorted(state.lots.items()):
            qty = sum((lot.qty for lot in lots), ZERO)
            if qty == 0:
                continue
            gross = sum((lot.gross for lot in lots), ZERO)
            cost = sum((lot.usd_cost for lot in lots), ZERO)
            inr = sum((lot.inr for lot in lots), ZERO)
            price = known.get(ticker)
            value = qty * price["price"] if price else None
            rows.append(
                {
                    "ticker": ticker,
                    "quantity": _qty(qty),
                    "cost_usd": fmt(cost),
                    "avg_price_usd": fmt(gross / qty, 4),
                    "inr_cost": fmt(inr),
                    "avg_inr_per_usd": fmt(inr / cost, 4) if cost else None,
                    "first_acquired": min(lot.acquired_at for lot in lots).isoformat(),
                    "lots": [
                        {
                            "trade_id": lot.trade_id,
                            "acquired_at": lot.acquired_at.isoformat(),
                            "quantity": _qty(lot.qty),
                            "cost_usd": fmt(lot.usd_cost),
                            "inr_cost": fmt(lot.inr),
                        }
                        for lot in lots
                        if lot.qty > 0
                    ],
                    "price": _price_json(price),
                    "market_value_usd": fmt(value),
                    "unrealized_usd": fmt(value - cost) if value is not None else None,
                }
            )
        pool = state.pool
        if not rows and pool.usd == 0:
            continue
        result.append(
            {
                "platform_id": platform_id,
                "name": names.get(platform_id, platform_id),
                "cash": {
                    "usd": fmt(pool.usd),
                    "inr_cost": fmt(pool.inr),
                    "avg_inr_per_usd": fmt(pool.inr / pool.usd, 4) if pool.usd > 0 else None,
                },
                "holdings": rows,
            }
        )
    return result


def _price_json(price: dict | None) -> dict | None:
    if not price:
        return None
    return {"price_usd": fmt(price["price"], 4), "as_of": price["as_of"], "origin": price["origin"]}


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


# ---- sell calculator -----------------------------------------------------------------------


def calculator(conn: sqlite3.Connection, rates: RateService, body) -> dict:
    built = ledger.build(conn)
    state = built.platforms.get(body.platform_id)
    lots = state.lots.get(body.ticker, []) if state else []
    if not lots:
        raise ValueError(f"You don't hold any {body.ticker} on this platform")
    slices = take_fifo(lots, body.quantity)

    now = datetime.now(IST)
    benchmark = rates.benchmark(now)
    rate = body.market_rate_override or benchmark.rate
    prefs = settings(conn)
    with_gst = (
        body.include_gst
        if body.include_gst is not None
        else prefs["estimate_conversion_gst"] == "1"
    )
    if body.exit_markup_pct is not None:
        markup, markup_source = body.exit_markup_pct, "entered"
    else:
        markup, markup_source = default_exit_markup(conn, body.platform_id)

    rule = ledger.fee_rule(conn, body.platform_id)
    estimated = estimate_fees(rule, "sell", body.quantity, body.price_usd)
    if body.brokerage_usd is not None:
        fees = TradeFees(
            body.brokerage_usd, body.brokerage_gst_usd or ZERO, body.other_fees_usd or ZERO
        )
    else:
        fees = estimated

    market = benchmark_json(benchmark)
    if body.market_rate_override:
        market |= {"rate": fmt(rate, 4), "source": "manual", "stale": False}

    exit_ = sell_shares(slices, body.quantity, body.price_usd, fees, rate, markup, with_gst)
    result = exit_json(exit_)
    be_price = break_even_price(slices, body.quantity, rule, rate, markup, with_gst)

    slab = body.slab_pct if body.slab_pct is not None else D(prefs.get("tax_slab_pct") or "30")
    tax = _tax_json(conn, rates, slices, now, exit_, slab) if body.include_tax else None
    held = sum((lot.qty for lot in lots), ZERO)
    net = D(result["net_inr"])
    return result | {
        "inputs": {
            "platform_id": body.platform_id,
            "ticker": body.ticker,
            "quantity": _qty(body.quantity),
            "price_usd": fmt(body.price_usd, 4),
            "exit_markup_pct": fmt(markup, 3),
            "exit_markup_source": markup_source,
            "include_gst": with_gst,
            "slab_pct": fmt(slab),
        },
        "market": market,
        "held_quantity": _qty(held),
        "fees": {
            "brokerage_usd": fmt(fees.brokerage_usd),
            "brokerage_gst_usd": fmt(fees.brokerage_gst_usd),
            "other_fees_usd": fmt(fees.other_fees_usd),
            "estimated": body.brokerage_usd is None,
        },
        "break_even_price_usd": fmt(be_price),
        "keep_in_usd": fmt(exit_.keep_in_usd),
        "tax": tax,
        "net_after_tax_inr": fmt(net - D(tax["tax_inr"])) if tax and tax["complete"] else None,
        "lots": [
            {
                "trade_id": s.trade_id,
                "acquired_at": s.acquired_at.isoformat(),
                "quantity": _qty(s.qty),
                "cost_usd": fmt(s.usd_cost),
                "inr_cost": fmt(s.inr),
            }
            for s in slices
        ],
    }


def exit_json(e: ExitBreakdown) -> dict:
    """All rupee figures, rounded part by part so received − cost = net = Σ parts exactly."""
    stock = q(e.stock_gain_inr)
    brokerage = q(e.brokerage_inr)
    currency = q(e.currency_inr)
    entry = q(e.entry_costs_inr)
    exit_markup = q(e.exit_markup_inr)
    gst = q(e.conversion_gst_inr)
    net = stock - brokerage + currency - entry - exit_markup - gst
    cost = q(e.inr_cost)
    received = cost + net
    gross_gain = stock + currency
    costs = brokerage + entry + exit_markup + gst
    return {
        "sale_gross_usd": fmt(e.sale_gross),
        "sell_fees_usd": fmt(e.sell_fees),
        "cash_usd": fmt(e.cash_usd),
        "usd_out": fmt(e.usd_out),
        "cost_usd": fmt(e.cost_usd),
        "inr_cost": fmt(cost),
        "market_inr": fmt(received + exit_markup + gst),
        "exit_markup_inr": fmt(exit_markup),
        "conversion_gst_inr": fmt(gst),
        "inr_received": fmt(received),
        "net_inr": fmt(net),
        "net_pct": fmt(net / cost * HUNDRED, 2) if cost else None,
        "parts": {
            "stock_gain_inr": fmt(stock),
            "brokerage_inr": fmt(brokerage),
            "currency_inr": fmt(currency),
            "entry_costs_inr": fmt(entry),
            "exit_markup_inr": fmt(exit_markup),
            "conversion_gst_inr": fmt(gst),
        },
        "gross_gain_inr": fmt(gross_gain),
        "costs_inr": fmt(costs),
        "costs_share_pct": fmt(costs / gross_gain * HUNDRED, 1) if gross_gain > 0 else None,
    }


def _tax_json(conn, rates: RateService, slices, sold_at: datetime, e: ExitBreakdown, slab):
    # Make sure SBI's archive is loaded (it covers every month-end since 2020).
    rates.bank_rate("sbi", datetime.combine(month_end_before(sold_at), datetime.max.time(), IST))
    estimate = estimate_capital_gains_tax(
        slices, sold_at, e.sale_gross, e.sell_fees, slab, ledger.sbi_tt_buying(conn)
    )
    return {
        "complete": estimate.complete,
        "short_term_gain_inr": fmt(estimate.short_term_inr),
        "long_term_gain_inr": fmt(estimate.long_term_inr),
        "taxable_short_inr": fmt(estimate.taxable_short_inr),
        "taxable_long_inr": fmt(estimate.taxable_long_inr),
        "tax_inr": fmt(estimate.tax_inr),
        "slab_pct": fmt(estimate.slab_pct),
        "lots": [
            {
                "trade_id": t.lot.trade_id,
                "acquired_at": t.lot.acquired_at.isoformat(),
                "quantity": _qty(t.lot.qty),
                "long_term": t.long_term,
                "sale_rate": fmt(t.sale_rate, 4),
                "cost_rate": fmt(t.cost_rate, 4),
                "gain_inr": fmt(t.gain_inr),
            }
            for t in estimate.lots
        ],
    }


# ---- dashboard ------------------------------------------------------------------------------


def fiscal_year(day: date) -> str:
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def tcs_tracker(entries: list[dict], today: date) -> list[dict]:
    """LRS remittances (₹ converted on deposits) and TCS paid, per financial year (Apr–Mar)."""
    years: dict[str, dict] = {}
    for entry in entries:
        if entry["kind"] != "deposit":
            continue
        fy = fiscal_year(entry["occurred_at"].astimezone(IST).date())
        bucket = years.setdefault(fy, {"remitted": ZERO, "tcs": ZERO, "count": 0})
        bucket["remitted"] += q(entry["inr_amount"] - entry["charges_inr"] - entry["tcs_inr"])
        bucket["tcs"] += q(entry["tcs_inr"])
        bucket["count"] += 1
    years.setdefault(fiscal_year(today), {"remitted": ZERO, "tcs": ZERO, "count": 0})
    return [
        {
            "fy": fy,
            "current": fy == fiscal_year(today),
            "remitted_inr": fmt(b["remitted"]),
            "tcs_paid_inr": fmt(b["tcs"]),
            "threshold_inr": fmt(TCS_THRESHOLD_INR),
            "remaining_inr": fmt(max(TCS_THRESHOLD_INR - b["remitted"], ZERO)),
            "over_threshold_inr": fmt(max(b["remitted"] - TCS_THRESHOLD_INR, ZERO)),
            "deposits": b["count"],
        }
        for fy, b in sorted(years.items(), reverse=True)
    ]


def dashboard(conn: sqlite3.Connection, rates: RateService) -> dict:
    built = ledger.build(conn)
    entries = forex.list_entries(conn)
    conversion = forex.summary(entries)
    names, known = platform_names(conn), ledger.prices(conn)
    prefs = settings(conn)
    with_gst = prefs["estimate_conversion_gst"] == "1"
    now = datetime.now(IST)
    try:
        benchmark = rates.benchmark(now)
    except Exception:  # the dashboard still shows realized figures offline
        benchmark = None

    sales = [(s, q(s.gain_inr)) for s in built.sales]
    dividends = [(d, q(d.income_inr)) for d in built.dividends]
    withdrawals = [
        {
            "currency": q(w.currency_inr),
            "entry": q(w.entry_cost_inr),
            "exit": q(w.exit_cost_inr),
        }
        for w in built.withdrawals
    ]
    stock_realized = sum((v for _, v in sales), ZERO)
    dividend_income = sum((v for _, v in dividends), ZERO)
    currency = sum((w["currency"] for w in withdrawals), ZERO)
    entry_costs = sum((w["entry"] for w in withdrawals), ZERO)
    exit_costs = sum((w["exit"] for w in withdrawals), ZERO)
    withdrawal_net = currency - entry_costs - exit_costs
    withheld_inr = sum((q(d.event.withheld_usd * d.event.market_rate) for d, _ in dividends), ZERO)

    brokerage = ZERO
    for event in ledger.stock_events(conn):
        if event.kind in ("buy", "sell") and event.fees:
            brokerage += q(event.fees * event.market_rate)

    deposits_in = sum(
        (q(e["inr_amount"] - e["tcs_inr"]) for e in entries if e["kind"] == "deposit"), ZERO
    )
    withdrawn = sum((q(e["inr_amount"]) for e in entries if e["kind"] == "withdrawal"), ZERO)

    positions = []
    for platform_id, state in sorted(built.platforms.items()):
        position = _position(conn, platform_id, state, known, benchmark, with_gst)
        if position:
            positions.append(position | {"name": names.get(platform_id, platform_id)})

    markup_in = D(conversion["deposits"]["markup_inr"])
    markup_out = D(conversion["withdrawals"]["markup_inr"])
    charges = D(conversion["deposits"]["charges_inr"]) + D(conversion["withdrawals"]["charges_inr"])
    return {
        "market": benchmark_json(benchmark),
        "invested_inr": fmt(deposits_in),
        "withdrawn_inr": fmt(withdrawn),
        "net_invested_inr": fmt(deposits_in - withdrawn),
        "realized": {
            "stock_gain_inr": fmt(stock_realized),
            "dividend_income_inr": fmt(dividend_income),
            "currency_inr": fmt(currency),
            "entry_costs_inr": fmt(entry_costs),
            "exit_costs_inr": fmt(exit_costs),
            "withdrawals_net_inr": fmt(withdrawal_net),
            "total_inr": fmt(stock_realized + dividend_income + withdrawal_net),
            "sales": len(sales),
            "withdrawals": len(withdrawals),
            "dividends": len(dividends),
        },
        "costs": {
            "forex_markup_in_inr": fmt(markup_in),
            "forex_markup_out_inr": fmt(markup_out),
            "conversion_charges_inr": fmt(charges),
            "brokerage_inr": fmt(brokerage),
            "total_inr": fmt(markup_in + markup_out + charges + brokerage),
            "us_tax_withheld_inr": fmt(withheld_inr),
        },
        "positions": positions,
        "tcs": tcs_tracker(entries, now.date()),
        "uncovered": [
            {
                "kind": event.kind,
                "id": event.id,
                "platform_id": event.platform,
                "occurred_at": event.at.isoformat(),
                "usd": fmt(amount),
            }
            for event, amount in built.uncovered
        ],
    }


def _position(conn, platform_id: str, state: PlatformState, known, benchmark, with_gst):
    held = {t: sum((lot.qty for lot in lots), ZERO) for t, lots in state.lots.items()}
    held = {t: qty for t, qty in held.items() if qty > 0}
    if not held and state.pool.usd == 0:
        return None
    missing = sorted(t for t in held if t not in known)
    position = {
        "platform_id": platform_id,
        "tickers": sorted(held),
        "missing_prices": missing,
        "cash_usd": fmt(state.pool.usd),
        "exit": None,
        "break_even_usd": None,
        "exit_markup_pct": None,
    }
    if missing or benchmark is None:
        return position
    markup, source = default_exit_markup(conn, platform_id)
    prices = {t: known[t]["price"] for t in held}
    exit_ = sell_everything(
        state, prices, ledger.fee_rule(conn, platform_id), benchmark.rate, markup, with_gst
    )
    exit_data = exit_json(exit_)
    target = break_even_value(D(exit_data["inr_cost"]), benchmark.rate, markup, with_gst)
    return position | {
        "exit": exit_data,
        "break_even_usd": fmt(target),
        "gap_usd": fmt(target - exit_.usd_out) if target is not None else None,
        "exit_markup_pct": fmt(markup, 3),
        "exit_markup_source": source,
    }
