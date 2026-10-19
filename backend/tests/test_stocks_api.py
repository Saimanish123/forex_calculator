"""Stocks, calculator, dashboard and data endpoints, end to end through the API."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from forex_ledger.config import IST

from .test_api import _walk

NOW = datetime.now(IST).replace(second=0, microsecond=0)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def deposit(client, days_ago=30, inr="97000", usd="1000", platform="indmoney"):
    r = client.post("/api/forex-entries", json={
        "kind": "deposit", "occurred_at": ts(days_ago), "platform_id": platform,
        "inr_amount": inr, "usd_amount": usd,
    })  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def trade(client, kind="buy", days_ago=20, ticker="ABC", qty="10", price="50", **extra):
    body = {"kind": kind, "occurred_at": ts(days_ago), "platform_id": "indmoney",
            "ticker": ticker} | extra  # fmt: skip
    if kind in ("buy", "sell"):
        body |= {"quantity": qty, "price_usd": price}
    return client.post("/api/stock-trades", json=body)


@pytest.fixture
def funded(client):
    deposit(client)
    assert trade(client, "buy", 20, qty="10", price="50", brokerage_usd="1.25",
                 brokerage_gst_usd="0.23").status_code == 201  # fmt: skip
    return client


def test_trade_lifecycle_and_holdings(funded):
    trades = funded.get("/api/stock-trades").json()["trades"]
    assert trades[0]["total_usd"] == "501.48" and trades[0]["fees_usd"] == "1.48"
    assert trades[0]["market"]["rate"] == "96.0000"

    sold = trade(funded, "sell", 10, qty="4", price="60").json()
    assert sold["realized"]["gain_usd"] == f"{Decimal('240') - Decimal('501.48') * 4 / 10:.2f}"

    holding = funded.get("/api/holdings").json()["platforms"][0]
    abc = holding["holdings"][0]
    assert (abc["ticker"], abc["quantity"]) == ("ABC", "6")
    assert abc["cost_usd"] == f"{Decimal('501.48') * 6 / 10:.2f}"
    assert holding["cash"]["usd"] == f"{Decimal(1000) - Decimal('501.48') + 240:.2f}"


def test_impossible_changes_are_rejected_and_rolled_back(funded):
    too_many = trade(funded, "sell", 10, qty="11", price="60")
    assert too_many.status_code == 400
    assert "Only 10 shares of ABC" in too_many.json()["detail"]

    sold = trade(funded, "sell", 10, qty="10", price="60").json()
    buy_id = next(t["id"] for t in funded.get("/api/stock-trades").json()["trades"]
                  if t["kind"] == "buy")  # fmt: skip
    before = funded.get("/api/stock-trades").json()

    assert funded.delete(f"/api/stock-trades/{buy_id}").status_code == 400
    shrink = {"kind": "buy", "occurred_at": ts(20), "platform_id": "indmoney", "ticker": "ABC",
              "quantity": "5", "price_usd": "50"}  # fmt: skip
    assert funded.put(f"/api/stock-trades/{buy_id}", json=shrink).status_code == 400
    later = shrink | {"quantity": "10", "occurred_at": ts(5)}  # buy moved after the sell
    assert funded.put(f"/api/stock-trades/{buy_id}", json=later).status_code == 400
    assert funded.get("/api/stock-trades").json() == before  # nothing changed

    assert funded.delete(f"/api/stock-trades/{sold['id']}").status_code == 204
    assert funded.delete(f"/api/stock-trades/{buy_id}").status_code == 204


def test_trade_validation(client):
    deposit(client)
    assert trade(client, ticker="bad ticker!").status_code == 400
    assert trade(client, qty="0").status_code == 422
    assert trade(client, qty="1.123456789").status_code == 422  # > 8 decimals
    assert trade(client, "split", split_from=1, split_to=1).status_code == 400
    assert trade(client, "split", split_from=1, split_to=2).status_code == 400  # nothing held
    lower = trade(client, ticker="msft").json()
    assert lower["ticker"] == "MSFT"


def test_split_and_adjustments_via_api(funded):
    assert trade(funded, "split", 15, split_from=1, split_to=4).status_code == 201
    assert funded.get("/api/holdings").json()["platforms"][0]["holdings"][0]["quantity"] == "40"
    merger = trade(funded, "adjustment", 12, qty_change="20", to_ticker="NEWCO",
                   cost_fraction="1", cancel_source=True)  # fmt: skip
    assert merger.status_code == 201, merger.text
    holdings = funded.get("/api/holdings").json()["platforms"][0]["holdings"]
    assert [(h["ticker"], h["quantity"], h["cost_usd"]) for h in holdings] == [
        ("NEWCO", "20", "501.48")
    ]
    bad = trade(funded, "adjustment", 11, qty_change="5", to_ticker="X", cost_fraction="0.5",
                cancel_source=True)  # fmt: skip
    assert bad.status_code == 400


def test_fee_estimate_endpoint(client):
    r = client.post(
        "/api/stock-trades/fees",
        json={"platform_id": "indmoney", "side": "sell", "quantity": "5", "price_usd": "236.50"},
    )
    assert r.json() == {"brokerage_usd": "2.96", "brokerage_gst_usd": "0.53",
                        "other_fees_usd": "0.00", "total_usd": "3.49"}  # fmt: skip


def test_prices(client, sources):
    assert client.put("/api/prices/abc", json={"price_usd": "12.3456"}).json()["price_usd"] == (
        "12.3456"
    )
    fetched = client.post("/api/prices/AAPL/fetch").json()
    assert (fetched["price_usd"], fetched["origin"]) == ("123.4500", "auto")
    assert client.post("/api/prices/NOPE/fetch").status_code == 404


def test_calculator_parts_add_up_exactly(funded):
    funded.put("/api/settings", json={"tax_slab_pct": "20"})
    r = funded.post(
        "/api/calculator",
        json={"platform_id": "indmoney", "ticker": "ABC", "quantity": "10", "price_usd": "55"},
    )
    assert r.status_code == 200, r.text
    c = r.json()
    p = {k: Decimal(v) for k, v in c["parts"].items()}
    net = Decimal(c["net_inr"])
    assert net == (
        p["stock_gain_inr"]
        - p["brokerage_inr"]
        + p["currency_inr"]
        - p["entry_costs_inr"]
        - p["exit_markup_inr"]
        - p["conversion_gst_inr"]
    )
    assert Decimal(c["inr_received"]) - Decimal(c["inr_cost"]) == net
    assert Decimal(c["market_inr"]) == (Decimal(c["inr_received"]) + p["exit_markup_inr"]
                                        + p["conversion_gst_inr"])  # fmt: skip
    assert c["inputs"]["exit_markup_source"] == "platform" and c["inputs"]["slab_pct"] == "20.00"
    assert c["fees"] == {"brokerage_usd": "1.38", "brokerage_gst_usd": "0.25",
                         "other_fees_usd": "0.00", "estimated": True}  # fmt: skip
    assert c["tax"] is not None and c["lots"][0]["quantity"] == "10"
    assert Decimal(c["break_even_price_usd"]) > 0

    actual = funded.post(
        "/api/calculator",
        json={
            "platform_id": "indmoney",
            "ticker": "ABC",
            "quantity": "10",
            "price_usd": "55",
            "brokerage_usd": "2",
            "brokerage_gst_usd": "0",
            "other_fees_usd": "0",
            "exit_markup_pct": "0.5",
            "include_gst": False,
            "include_tax": False,
        },
    ).json()
    assert actual["fees"]["estimated"] is False and actual["sell_fees_usd"] == "2.00"
    assert actual["conversion_gst_inr"] == "0.00" and actual["tax"] is None

    too_many = funded.post(
        "/api/calculator",
        json={"platform_id": "indmoney", "ticker": "ABC", "quantity": "11", "price_usd": "55"},
    )
    assert too_many.status_code == 400
    not_held = funded.post(
        "/api/calculator",
        json={"platform_id": "vested", "ticker": "ABC", "quantity": "1", "price_usd": "55"},
    )
    assert not_held.status_code == 400


def test_dividends_flow_through_the_ledger(client):
    r = client.post(
        "/api/forex-entries",
        json={
            "kind": "dividend",
            "occurred_at": ts(3),
            "platform_id": "indmoney",
            "ticker": "voo",
            "gross_usd": "3.48",
            "withheld_usd": "0.87",
        },
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert (d["ticker"], d["usd_amount"], d["income_inr"]) == ("VOO", "2.61", "250.56")
    data = client.get("/api/forex-entries").json()
    assert data["entries"][0]["kind"] == "dividend"
    assert data["summary"]["deposits"]["count"] == 0  # conversions only
    bad = client.post(
        "/api/forex-entries",
        json={
            "kind": "dividend",
            "occurred_at": ts(3),
            "platform_id": "indmoney",
            "ticker": "VOO",
            "gross_usd": "1.00",
            "withheld_usd": "1.00",
        },
    )
    assert bad.status_code == 400
    assert client.get("/api/dashboard").json()["realized"]["dividend_income_inr"] == "250.56"


def test_sample_data_dashboard_and_tcs(client):
    assert client.get("/api/meta").json()["empty"] is True
    assert client.post("/api/sample-data").status_code == 201
    assert client.post("/api/sample-data").status_code == 400
    assert client.get("/api/meta").json()["empty"] is False
    d = client.get("/api/dashboard").json()
    r = {k: Decimal(v) for k, v in d["realized"].items() if isinstance(v, str)}
    assert r["total_inr"] == r["stock_gain_inr"] + r["dividend_income_inr"] + (
        r["currency_inr"] - r["entry_costs_inr"] - r["exit_costs_inr"]
    )
    assert d["invested_inr"] == "300450.00"  # all ₹ debited (2,00,270 + 1,00,180); TCS excluded
    position = d["positions"][0]
    assert position["missing_prices"] == [] and position["exit"] is not None
    fys = {t["fy"]: t for t in d["tcs"]}
    assert fys["2024-25"]["remitted_inr"] == "200000.00"
    assert fys["2025-26"]["remitted_inr"] == "100000.00"
    assert d["uncovered"] == []
    for path in ("/api/dashboard", "/api/holdings", "/api/stock-trades", "/api/forex-entries"):
        assert not any(isinstance(v, float) for v in _walk(client.get(path).json())), path


def test_export_import_round_trip_and_safety(client):
    client.post("/api/sample-data")
    client.put("/api/settings", json={"tax_slab_pct": "20", "estimate_conversion_gst": False})
    exported = client.get("/api/export")
    assert "attachment" in exported.headers["content-disposition"]
    snapshot = exported.json()

    assert client.post("/api/import", json={"format": "other"}).status_code == 400
    sell_only = [t for t in snapshot["tables"]["stock_trades"] if t["kind"] == "sell"]
    broken = snapshot | {"tables": snapshot["tables"] | {"stock_trades": sell_only}}  # no buys
    assert client.post("/api/import", json=broken).status_code == 400
    assert client.get("/api/export").json()["tables"] == snapshot["tables"]  # untouched

    assert client.post("/api/delete-all", json={"confirm": "yes"}).status_code == 422
    assert client.post("/api/delete-all", json={"confirm": "DELETE"}).status_code == 200
    assert client.get("/api/meta").json()["empty"] is True

    result = client.post("/api/import", json=snapshot)
    assert result.status_code == 200, result.text
    assert client.get("/api/export").json()["tables"] == snapshot["tables"]
    assert client.get("/api/meta").json()["settings"]["estimate_conversion_gst"] == "0"


def test_csv_exports(client):
    client.post("/api/sample-data")
    conversions = client.get("/api/export/conversions.csv")
    assert conversions.headers["content-type"].startswith("text/csv")
    lines = conversions.text.strip().splitlines()
    assert lines[0].startswith("date_time_ist,type,platform") and len(lines) == 5
    trades = client.get("/api/export/trades.csv").text.strip().splitlines()
    assert len(trades) == 5 and "sell" in trades[-1]
    assert client.get("/api/export/other.csv").status_code == 404


def test_future_trades_and_bad_dividend_tickers_are_rejected(funded):
    future = (NOW + timedelta(days=2)).isoformat()
    split = {"kind": "split", "occurred_at": future, "platform_id": "indmoney", "ticker": "ABC",
             "split_from": 1, "split_to": 2}  # fmt: skip
    assert funded.post("/api/stock-trades", json=split).status_code == 400
    bad = funded.post(
        "/api/forex-entries",
        json={
            "kind": "dividend",
            "occurred_at": ts(1),
            "platform_id": "indmoney",
            "ticker": "$$$",
            "gross_usd": "1.00",
        },
    )
    assert bad.status_code == 400


def test_blank_dividend_ticker_rejected(client):
    r = client.post(
        "/api/forex-entries",
        json={
            "kind": "dividend",
            "occurred_at": ts(1),
            "platform_id": "indmoney",
            "ticker": "   ",
            "gross_usd": "1.00",
        },
    )
    assert r.status_code == 400


def test_back_to_back_backups_never_overwrite(client, tmp_path):
    client.post("/api/sample-data")
    first = client.post("/api/backup").json()["path"]
    second = client.post("/api/delete-all", json={"confirm": "DELETE"}).json()["backup"]
    assert first != second
    assert len(list((tmp_path / "backups").glob("forex-*.db"))) == 2


def test_trade_preview_never_saves(funded):
    before = funded.get("/api/stock-trades").json()
    body = {"kind": "sell", "occurred_at": ts(10), "platform_id": "indmoney", "ticker": "ABC",
            "quantity": "4", "price_usd": "60", "brokerage_usd": "0.60",
            "brokerage_gst_usd": "0.11"}  # fmt: skip
    p = funded.post("/api/stock-trades/preview", json=body).json()
    assert (p["id"], p["gross_usd"], p["fees_usd"], p["total_usd"]) == (None, "240.00", "0.71",
                                                                         "239.29")  # fmt: skip
    assert p["realized"]["gain_usd"] == f"{Decimal('239.29') - Decimal('501.48') * 4 / 10:.2f}"
    too_many = funded.post("/api/stock-trades/preview", json=body | {"quantity": "11"})
    assert too_many.status_code == 400 and "Only 10 shares" in too_many.json()["detail"]

    buy_id = before["trades"][0]["id"]
    edit = {"kind": "buy", "occurred_at": ts(20), "platform_id": "indmoney", "ticker": "ABC",
            "quantity": "12", "price_usd": "50"}  # fmt: skip
    p = funded.post(f"/api/stock-trades/preview?trade_id={buy_id}", json=edit).json()
    assert (p["id"], p["total_usd"]) == (buy_id, "600.00")
    assert funded.get("/api/stock-trades").json() == before  # nothing saved
