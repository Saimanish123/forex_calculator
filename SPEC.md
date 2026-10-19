# Forex & US Stocks Ledger — Spec

A personal app for Indian residents investing in US stocks (INDmoney by default, other
platforms supported). It records every INR↔USD conversion and every stock trade, then
shows the **hidden costs** (forex markup, GST, brokerage), the **currency effect**, and
the **true rupee profit** of any holding — including a "what if I sell now" calculator.

Status: **phase 1 (forex core) built; phases 2–3 next.**

---

## 1. Goals and non-goals

**Goals**
- Ledger-style record of conversions (deposits/withdrawals) and stock trades, including past ones.
- For every conversion: the rate you got vs. the mid-market rate → markup in ₹ and %.
- Compare each conversion with what SBI / ICICI (or a typed-in bank rate) would have given.
- Sell calculator: given stock, quantity and current price → net ₹ profit after brokerage,
  forex markups, GST and currency movement, plus break-even price.
- Dashboard: total hidden costs, break-even, "if I sold everything today", TCS tracker.

**Non-goals (for now)**
- No live link to INDmoney or any broker (no public API) — you type the numbers.
- No live stock prices — you enter the current price in the calculator (auto-fetch is a later option).
- No hosting / sync between laptops — each laptop has its own local data file (export/import for backup).
- The tax figure is an **estimate**, not tax advice.

---

## 2. How it runs

- Local web app: Python 3.11 + FastAPI + SQLite, plain HTML/JS frontend (no Node build step).
- Start with one command (e.g. `uv run forex`) → opens `http://localhost:8000` in the browser.
- Data lives in a single SQLite file in the project folder. It persists across server restarts,
  browser restarts and reboots (nothing important is stored in the browser).
- **Backups**: on every start the app copies the database to a dated file in `backups/`
  (keeps the last 30). Optional second backup folder (e.g. iCloud / Google Drive) set in Settings
  — only backup copies go there, never the live database (sync can corrupt an open SQLite file).
- CSV/JSON export & import for moving to another laptop.
- The database and backups are git-ignored so personal financial data is never committed.
- Optional double-click launchers (macOS `.command`, Windows `.bat`) so the terminal isn't needed.

### Distribution (public GitHub repo)
- Anyone who clones gets **code only**; the app creates a fresh empty database on first run.
- `.gitignore` (database, `backups/`, `.env`, caches) is in place before the first commit;
  `git status` is checked before every push so no personal data is ever committed.
- README: what it does, 3-step setup (install `uv` → clone → `uv run forex`), screenshots,
  data sources with attribution, disclaimer.
- Disclaimer in README and app footer: not financial or tax advice; rates/fees are estimates.
- No personal data, paths or emails in code; fee defaults are generic and editable per user.
- Rate sources are fetched politely (cached; ICICI page at most once per day per user).
- "Load sample data" demo mode to try the app before entering real numbers.
- License: MIT.

---

## 3. Platforms

Every entry has a platform; **INDmoney is the default**. Each platform has editable fee settings
that only *pre-fill* forms — the actual figure from your statement / contract note always wins.

| Platform | Brokerage (default) | GST on brokerage | Expected forex markup | Other |
|---|---|---|---|---|
| INDmoney | 0.25% per trade, max $25 | 18% | ~1% (stated range 0.5–1.2%) | no deposit/withdrawal fee |
| Vested | per plan (user sets) | 18% | user sets | wire fee ₹500–3,000 |
| Interactive Brokers | $0.0035/share, min $0.35 | 0% | user's bank LRS rate | — |
| ICICI Direct Global | $0–2.75/trade | 18% | ICICI rate | — |
| Custom | user defines | user defines | user defines | optional per-sell fee |

Dollars are tracked **per platform** (dollars sitting in INDmoney can't buy on Vested).

### User defaults (Settings)
- **Default platform** (INDmoney unless changed) — pre-fills every entry; drives fee rules,
  expected markup and which dollar pool is used.
- **Default bank** — pre-fills every entry; decides which bank's rates are fetched and shown as the
  comparison. For a "direct bank LRS" platform it is also the actual conversion rate source.
  For INDmoney / GIFT City platforms the bank is comparison only (the platform converts).
- Both can be overridden per entry. Mid-market is always fetched regardless of these choices.
- The tax estimate always uses SBI TT rates (Rule 115), independent of the default bank.
- Each bank is a small rate-source adapter (SBI, ICICI automatic; others manual until an adapter
  exists), so adding a bank doesn't touch the rest of the app.

---

## 4. Ledgers (what you enter)

You always enter **what actually moved** in your bank / wallet. Entries can be back-dated;
entries on the same date are ordered by an optional time (deposits before buys by default).

### 4.1 Forex ledger
| Type | Fields |
|---|---|
| **Deposit** (INR→USD) | date + time, platform, ₹ debited from bank, $ credited, *of which* GST/charges ₹ (optional), *of which* TCS ₹ (optional), note |
| **Withdrawal** (USD→INR) | date + time, platform, $ withdrawn, ₹ received in bank, *of which* GST/charges ₹ (optional), note |
| **Dividend** | date, platform, ticker, gross $, US tax withheld $ (default 25%), net $ credited |

The app fetches the mid-market rate for the date (editable override) and stores it with the entry.

### 4.2 Stock ledger
| Type | Fields |
|---|---|
| **Buy** | date, platform, ticker, quantity (fractional ok), price/share $, brokerage $ + GST $ (auto-filled, editable), note |
| **Sell** | same fields |
| **Split** | date, platform, ticker, ratio (e.g. 10-for-1) — multiplies quantity and divides per-share cost of all open lots; total cost unchanged |
| **Adjustment** | date, platform, ticker, quantity change and/or cost change, note — for mergers, spin-offs and other rare corporate actions |

Sells consume holdings **FIFO** (oldest shares first — same as Indian tax practice, matters
for the 24-month long-term rule).

### 4.3 Portfolio snapshot (optional)
Current price per ticker, entered by you, used by the dashboard's "if I sold everything today".

---

## 5. Rate sources

| Data | Source | Coverage | Notes |
|---|---|---|---|
| Mid-market — at the entry's timestamp (primary) | Yahoo Finance chart API `INR=X` (unofficial, no key) | 1-min bars for last 30 days, 5-min for 60 days, 1-hour for ~2 years | verified working; may break → fall back below |
| Mid-market — today (fallback) | open.er-api.com | latest, hourly | free, no key |
| Mid-market — past dates (fallback) | Frankfurter (ECB-based) | back to 1999 | daily fix ~16:00 CET; weekends/holidays → previous business day |
| SBI TT buy/sell | `sahilgupta/sbi-fx-ratekeeper` CSV on GitHub | back to 2020 | community archive, occasional gaps; also used for tax (Rule 115) |
| ICICI card rates | ICICI forex card-rate web page | today only | app saves a snapshot daily → builds its own history from first run |
| HDFC / any other bank | typed in | — | HDFC's daily PDF URL isn't stable; manual entry |

Every forex entry has a **date and time (IST)** — the moment the rate was locked (INDmoney's
confirm time). A "Now" button fills the current time. The benchmark is the 1-minute bar
containing that timestamp when available, else the finest available bar; the entry stores which
granularity was used. The finest bar already cached always wins, so editing an old entry never
downgrades its precision. Only finished bars are cached.

**Measured coverage (12–16 Oct 2026):** Yahoo's `INR=X` 1-minute data is dense from 09:00 to
15:30 IST on weekdays, with only 6–20 scattered quotes outside those hours per day, and nothing
at weekends. A conversion more than 30 minutes (or two bars) after the last quote is flagged
"stale" and compared with that last quote; the UI shows the quote's time.

**Bank rate sources (measured):** SBI's CSV on raw GitHub took 30–75 s per request from India,
while the jsDelivr mirror took ~1.3 s (but may lag by up to 12 h). Requests use the mirror; the
background warm-up at startup also tries raw GitHub. Live calls time out after 6 s, and failed
fetches are retried after 10 minutes.

All fetched rates are cached locally, so each date is fetched once. Past entries get mid-market
and SBI comparisons automatically; ICICI comparison only exists from the day the app started
collecting (or if typed in).

---

## 6. Calculations

Notation: `m(d)` = mid-market ₹ per $ on date `d`.

### 6.1 Per conversion
- **Deposit**: converted ₹ = debited − GST/charges − TCS. Rate got `r = converted / $`.
  Markup ₹ = converted − `$ × m(d)`; markup % = `r / m(d) − 1`. Hidden cost = markup + GST/charges.
  TCS is shown separately (claimable in ITR — not a loss).
- **Withdrawal**: hidden cost = `$ × m(d)` − ₹ received (split into markup and GST if GST entered).
- **Bank comparison**: "SBI/ICICI would have given X" using that bank's TT sell (deposit) or TT buy
  (withdrawal) rate for the date → "INDmoney saved / cost you ₹Y".

### 6.2 Dollar pool (per platform, average cost)
Each platform keeps: `B` = $ balance, `C` = all-in ₹ cost of those dollars, `M` = market-value
basis (₹ at mid-market when acquired).
- Deposit: `B += $`, `C += converted + GST/charges`, `M += $ × m(d)`.
- Stock buy (cash out `X` = qty × price + brokerage + GST): the lot takes `X/B` share of `C` and `M`
  → every lot has a $ cost, an all-in ₹ cost and a market ₹ basis.
- Stock sell (net proceeds `N`, realized $ gain `G`): `B += N`, `C += lots' ₹ cost + G × m(d)`,
  `M += lots' market basis + G × m(d)`.
- Dividend (net `$`): `B += $`, `C += $ × m(d)`, `M += $ × m(d)`.
- Withdrawal of `$w` at `m(d)`, receiving `₹w`: share `s = $w / B` of `C` and `M` leaves the pool.
  Realized result = `₹w − s·C`, split as
  **currency movement** `$w·m(d) − s·M` − **exit cost** `($w·m(d) − ₹w)` − **entry cost** `(s·C − s·M)`.
- If a buy/withdrawal exceeds `B` (missing earlier entries), the shortfall is valued at `m(d)` with
  zero markup and the entry is flagged ⚠️.

Check (example from discussion): buy $1,000 @ ₹97 (mkt 96), withdraw $1,000 @ ₹98 (mkt 99)
→ currency +₹3,000, entry cost −₹1,000, exit cost −₹1,000, **net +₹1,000**.

### 6.3 Sell calculator
Inputs: ticker, quantity, current price; auto-filled (editable): today's `m`, platform brokerage
rule, expected exit markup `k` (your average past withdrawal markup on that platform, else the
platform default).

```
Sale value S = qty × price;  N = S − sell brokerage − GST − other sell fees
₹ received   = N × m × (1 − k) − GST on conversion
₹ cost       = FIFO lots' all-in ₹ cost (H dollars, cost e per $, market basis m0 per $)
Net profit   = ₹ received − ₹ cost
  = stock gain (S − lots' gross buy value) × m
  − brokerage (buy + sell, incl GST) × m
  + currency movement H × (m − m0)
  − entry markup H × (e − m0)
  − exit markup N × m × k
  − GST on conversion
```
Also shows:
- **Break-even sell price** at today's rate (solved numerically).
- **"Keep dollars in wallet"** result: `N − H` in $ (no exit conversion).
- **Fees as % of gross gain**: total costs ÷ (stock gain + currency movement).
- **Tax estimate** (toggle): STCG at your slab rate (held ≤ 24 months) or LTCG 12.5% (> 24 months,
  no ₹1.25L exemption), on the ₹ gain computed with SBI TT buying rates (last day of the month
  before purchase / sale month — common practice under Rule 115). Labelled "estimate — confirm with CA".

Reference example (must be reproduced by tests): 10 shares bought @ $100, buy brokerage+GST
$2.95, dollars cost ₹97 each (mkt 96); sell 10 @ $110 with mkt 98.50, exit markup 1%,
INDmoney fees → net **≈ ₹9,476**; break-even ≈ **$100.24/share**; keep-in-USD **+$93.80**.

### 6.4 GST on currency conversion (default slab, editable)
On gross conversion value `V` (₹): `V ≤ 1L` → 0.18% × V (min ₹45); `1L–10L` → ₹180 + 0.09% × (V − 1L);
`> 10L` → ₹990 + 0.018% × (V − 10L), max ₹10,800. Used only for estimates — entered actuals win.

### 6.5 Portfolio break-even
For each platform: the portfolio $ value at which converting everything today (at `m × (1 − k)`
minus GST) returns the net ₹ still invested. Shown with how far current value is from it.

### 6.6 TCS tracker
Per financial year (Apr–Mar), sum of deposits across all platforms vs ₹10 lakh threshold;
TCS paid so far (claimable in ITR) and remaining headroom.

---

### 6.7 Accuracy labels
Every figure in the UI is labelled **exact** or **≈ estimate**:
- **Exact** (given correct entries): amounts paid/received, rate got, ₹ cost of holdings,
  realized ₹ P&L of completed round trips, brokerage / GST / TCS totals.
- **≈ Close**: markup vs mid-market. With a 1-minute benchmark (entries within 30 days) the error
  is a few paise (~₹10–50 per ₹1L) — as exact as a market rate can be, since providers differ by a
  few paise at any instant. Coarser bars for older entries widen this (1-hour ~₹50–150, daily
  ~₹100–300 per ₹1L); the entry shows its benchmark granularity. Conversions outside ~09:00–15:30
  IST are compared with the last available quote (flagged with its time). Bank comparisons
  compare exchange rates only (a direct bank transfer would also add GST and a wire fee), and
  published card rates may differ from the user's actual bank rate. The split of P&L into stock /
  currency / costs (total exact; split depends on average-cost + FIFO convention).
- **≈ Estimate**: sell calculator, break-even, "if sold today" (expected exit markup, future
  settlement rate, GST slab) and the tax estimate.

## 7. Screens

1. **Dashboard** — totals invested/withdrawn, holdings with ₹ cost, total hidden cost (by type &
   platform), portfolio break-even, "if I sold everything today" breakdown, TCS tracker.
2. **Forex ledger** — table of deposits/withdrawals/dividends with rate got, mid-market, markup ₹/%,
   bank comparison; add/edit/delete form.
3. **Stock ledger** — table of buys/sells; holdings view (per ticker: qty, avg $ price, ₹ cost, lots).
4. **Sell calculator** — the breakdown in §6.3.
5. **Rates** — today's mid-market, SBI, ICICI (+ typed-in banks) with markup vs mid-market.
6. **Settings** — default platform, default bank, platforms & fee rules, default exit markup,
   GST slab, tax slab rate, backup folder, export/import.

---

## 8. Build phases

1. **Forex core** — rate sources + cache, forex ledger, per-conversion cost, bank comparison, Rates screen.
2. **Stocks** — stock ledger, dollar pools, FIFO lots, holdings, sell calculator, break-even.
3. **Extras** — dividends, tax estimate, TCS tracker, dashboard, CSV/JSON export & import.

Each phase ships working and is tested against the worked examples above.

---

## 9. Decided defaults (easy to change)

- Tax estimate: **included**, toggle, labelled as estimate.
- Dividends: **included** as a forex-ledger entry type.
- Lot matching: FIFO for shares; average cost for dollars.
- Exit markup for projections: your own history on that platform, else platform default (~1%).
