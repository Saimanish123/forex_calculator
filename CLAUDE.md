# CLAUDE.md

## What this is

A local web app for Indian residents investing in US stocks (INDmoney by default). Users record
INR↔USD conversions and stock trades; the app shows hidden forex markup, GST, brokerage, currency
effect and true ₹ profit, plus a sell calculator. **[SPEC.md](SPEC.md) is the source of truth** for
features, formulas (§6) and build phases (§8). Update it when a decision changes.

## Stack and commands

Python 3.11+, FastAPI, SQLite, plain HTML/JS frontend (no Node build step). Managed with `uv`.

```bash
uv sync                 # install deps
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format .    # format
```

## Conventions

- Money and rates use `Decimal`, never float. Rates are always ₹ per $ (e.g. 94.96).
- Store only what the user entered plus the rates fetched for each entry. Everything derived
  (markups, dollar pools, FIFO lots, P&L) is recomputed from the ledger, so back-dated entries
  just work.
- Keep calculation logic pure (no DB/HTTP) so it's easy to test. The worked examples in SPEC.md §6
  are test cases and must keep passing.
- External rate sources are cached locally. Fetch each date once; ICICI's page at most once a day.
- The repo is public. Never commit personal data, database files or real ledger entries
  (use the sample-data mode for demos).
