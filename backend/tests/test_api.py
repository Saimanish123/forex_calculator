import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from forex_ledger.config import IST


def _deposit(**overrides):
    at = datetime.now(IST).replace(hour=10, minute=30, second=0, microsecond=0)
    if at > datetime.now(IST):
        at -= timedelta(days=1)
    return {
        "kind": "deposit",
        "occurred_at": at.isoformat(),
        "platform_id": "indmoney",
        "bank_id": "sbi",
        "inr_amount": "97000",
        "usd_amount": "1000",
    } | overrides


def test_meta_has_defaults(client):
    meta = client.get("/api/meta").json()
    assert meta["settings"]["default_platform"] == "indmoney"
    assert {p["id"] for p in meta["platforms"]} >= {"indmoney", "vested", "ibkr"}
    banks = {b["id"]: b for b in meta["banks"]}
    assert banks["hdfc"]["auto"] and not banks["axis"]["auto"]


def test_create_list_update_delete_entry(client):
    created = client.post("/api/forex-entries", json=_deposit())
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["market"] | {"bar_at": None} == {
        "rate": "96.0000",
        "source": "yahoo",
        "interval": "1m",
        "bar_at": None,
        "stale": False,
    }
    assert entry["analysis"]["markup_inr"] == "1000.00"
    assert entry["analysis"]["markup_pct"] == "1.042"

    listing = client.get("/api/forex-entries").json()
    assert len(listing["entries"]) == 1
    assert listing["summary"]["hidden_cost_inr"] == "1000.00"
    assert listing["summary"]["deposits"]["avg_markup_pct"] == "1.042"

    updated = client.put(f"/api/forex-entries/{entry['id']}", json=_deposit(inr_amount="96500"))
    assert updated.json()["analysis"]["markup_inr"] == "500.00"

    assert client.delete(f"/api/forex-entries/{entry['id']}").status_code == 204
    assert client.get("/api/forex-entries").json()["entries"] == []


def test_manual_overrides_are_kept_on_refresh(client):
    entry = client.post(
        "/api/forex-entries",
        json=_deposit(market_rate_override="95.5", bank_rate_override="97.25"),
    ).json()
    assert entry["market"]["source"] == "manual"
    refreshed = client.post(f"/api/forex-entries/{entry['id']}/refresh-rates").json()
    assert refreshed["market"]["rate"] == "95.5000"
    assert refreshed["bank"] == entry["bank"]
    assert refreshed["analysis"]["vs_bank_inr"] == "250.00"


def test_preview_does_not_save(client):
    preview = client.post("/api/forex-entries/preview", json=_deposit(kind="withdrawal"))
    assert preview.status_code == 200
    assert preview.json()["id"] is None
    assert client.get("/api/forex-entries").json()["entries"] == []


def test_validation_errors(client):
    assert client.post("/api/forex-entries", json=_deposit(usd_amount="0")).status_code == 422
    too_much_tcs = _deposit(tcs_inr="97000")
    assert client.post("/api/forex-entries", json=too_much_tcs).status_code == 400
    assert client.post("/api/forex-entries", json=_deposit(platform_id="nope")).status_code == 400
    future = _deposit(occurred_at=(datetime.now(IST) + timedelta(days=1)).isoformat())
    assert client.post("/api/forex-entries", json=future).status_code == 400


def test_settings_platforms_and_banks(client):
    assert client.put("/api/settings", json={"default_bank": "nope"}).status_code == 404
    assert (
        client.put("/api/settings", json={"default_bank": "hdfc"}).json()["default_bank"] == "hdfc"
    )

    bank = client.post("/api/banks", json={"name": "Yes Bank"}).json()
    assert bank["id"] == "yes_bank" and not bank["auto"]

    platform = client.post("/api/platforms", json={"name": "Vested", "brokerage_pct": "0.1"})
    assert platform.json()["id"] == "vested_2"
    edited = client.put("/api/platforms/indmoney", json={"name": "INDmoney", "kind": "gift_city"})
    assert edited.json()["expected_markup_pct"] == "1"


def test_rates_today(client):
    today = client.get("/api/rates/today").json()
    assert today["mid"]["rate"] == "96.0000"
    banks = {b["id"]: b for b in today["banks"]}
    assert banks["sbi"]["rate"]["buy_usd_markup_pct"] == "0.521"
    assert banks["hdfc"]["rate"] is None and banks["hdfc"]["status"]["ok"] == 0
    assert banks["axis"]["status"] is None

    saved = client.post(
        "/api/bank-rates",
        json={
            "bank_id": "axis",
            "rate_date": datetime.now(IST).date().isoformat(),
            "tt_buy": "95",
            "tt_sell": "97",
        },
    )
    assert saved.status_code == 201


def _walk(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def test_no_floats_anywhere_in_responses(client):
    client.post(
        "/api/forex-entries", json=_deposit(charges_inr="180.55", bank_rate_override="97.1234")
    )
    client.post("/api/forex-entries", json=_deposit(kind="withdrawal", inr_amount="94123.45"))
    for path in ("/api/forex-entries", "/api/rates/today", "/api/meta"):
        values = list(_walk(client.get(path).json()))
        assert values and not any(isinstance(v, float) for v in values), path


def test_list_totals_equal_sum_of_rows(client):
    for inr, usd in (("97000.33", "1000.07"), ("12345.67", "130.11"), ("555.55", "5.79")):
        client.post("/api/forex-entries", json=_deposit(inr_amount=inr, usd_amount=usd))
    data = client.get("/api/forex-entries").json()
    rows = sum(Decimal(e["analysis"]["hidden_cost_inr"]) for e in data["entries"])
    assert Decimal(data["summary"]["hidden_cost_inr"]) == rows


def test_timezones_are_normalized_to_ist(client):
    ist = client.post("/api/forex-entries", json=_deposit()).json()
    utc_time = datetime.fromisoformat(ist["occurred_at"]).astimezone(UTC).isoformat()
    utc = client.post("/api/forex-entries", json=_deposit(occurred_at=utc_time)).json()
    naive = client.post(
        "/api/forex-entries", json=_deposit(occurred_at=ist["occurred_at"][:19])
    ).json()
    assert ist["occurred_at"] == utc["occurred_at"] == naive["occurred_at"]
    assert ist["occurred_at"].endswith("+05:30")
    assert ist["market"] == utc["market"] == naive["market"]


def test_amount_precision_is_enforced_and_stored_exactly(client):
    assert (
        client.post("/api/forex-entries", json=_deposit(inr_amount="97000.005")).status_code == 422
    )
    too_precise_rate = _deposit(market_rate_override="96.12345")
    assert client.post("/api/forex-entries", json=too_precise_rate).status_code == 422
    entry = client.post("/api/forex-entries", json=_deposit(inr_amount="1e5")).json()
    assert entry["inr_amount"] == "100000.00"


def test_update_keeps_creation_time_and_unknown_ids_404(client, tmp_path):
    entry = client.post("/api/forex-entries", json=_deposit()).json()
    client.put(f"/api/forex-entries/{entry['id']}", json=_deposit(note="edited"))
    conn = sqlite3.connect(tmp_path / "forex.db")
    created, updated, inr = conn.execute(
        "SELECT created_at, updated_at, inr_amount FROM forex_entries"
    ).fetchone()
    assert created <= updated and inr == "97000"
    assert client.delete("/api/forex-entries/999").status_code == 404
    assert client.put("/api/forex-entries/999", json=_deposit()).status_code == 404


def test_frontend_is_served_with_revalidation(client):
    page = client.get("/")
    script = client.get("/js/app.js")
    assert page.status_code == script.status_code == 200
    assert script.headers["cache-control"] == "no-cache"
    again = client.get("/js/app.js", headers={"If-None-Match": script.headers["etag"]})
    assert again.status_code == 304
