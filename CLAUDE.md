# CLAUDE.md

## What this is

A local web app for Indian residents investing in US stocks (INDmoney by default). Users record
INR↔USD conversions and stock trades; the app shows hidden forex markup, GST, brokerage, currency
effect and true ₹ profit, plus a sell calculator. **[SPEC.md](SPEC.md) is the source of truth** for
features, formulas (§6), measured data-source limits (§5) and build phases (§8). Update it when a
decision changes.

## Layout

- `backend/forex_ledger/` — FastAPI app, SQLite (`migrations/*.sql`, applied by `PRAGMA user_version`)
  - `calc/` pure money math: `conversions` (markups), `portfolio` (ledger replay: dollar pools,
    FIFO lots, realized results), `scenario` (sell / sell-everything / break-even), `tax`, `fees`, `gst`
  - `rates/` one parser per external source + caching `RateService`
  - `forex.py` / `stocks.py` ledgers (stocks replays the ledger in-transaction to reject impossible
    edits) · `ledger.py` DB rows → engine events · `insights.py` holdings, calculator, dashboard,
    TCS · `transfer.py` export/import/sample data · `app.py` API + static frontend · `cli.py`
- `backend/tests/` — pytest; `fixtures/` are trimmed real rate sheets; network is always faked
- `frontend/` — no build step: Preact + htm vendored in `vendor/`, ES modules in `js/pages/`,
  shared tokens/components in `css/app.css` + `js/components/ui.js`, page CSS in `css/*.css`

## Commands

```bash
uv run forex            # run the app (data in ./data, override with FOREX_LEDGER_DATA_DIR)
uv run pytest           # tests (includes property-based tests via hypothesis)
uv run ruff check .     # lint
uv run ruff format .    # format
```

## Conventions

- Money and rates are `Decimal`, never float — in Python, in SQLite (TEXT) and in JSON (strings via
  `money.fmt`; FastAPI would turn Decimals into floats). The frontend never does money math.
- Amounts have ≤ 2 decimals, rates ≤ 4 (rejected otherwise). Fetched rates are stored at 4 decimals
  so the rate shown is the rate used. Per-conversion ₹ figures are rounded to the paisa once
  (`forex.paise`) and totals are sums of those, so totals always equal the rows.
- Store only user input plus the rates fetched for each entry or trade; everything else is
  derived by replaying the ledger, so back-dated entries just work. Engine invariants (realized +
  "if sold today" = out − in) are property-tested in `test_properties.py` — keep them passing.
- External sources: one parser per source (a layout change breaks only that source), cached
  locally, short timeouts on request paths, failures retried after 10 minutes.
- Every claim about data sources in SPEC.md/UI copy is backed by a measurement; keep it that way.
- The repo is public: never commit personal data, databases, screenshots or scratch files.
