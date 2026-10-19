-- Money and rates are stored as TEXT decimals (exact). Timestamps are ISO 8601 with offset.
-- Only user input and fetched rates are stored; everything else is derived (SPEC §6).

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE platforms (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    kind                     TEXT NOT NULL CHECK (kind IN ('gift_city', 'us_broker', 'bank_lrs')),
    brokerage_pct            TEXT NOT NULL DEFAULT '0',
    brokerage_min_usd        TEXT NOT NULL DEFAULT '0',
    brokerage_max_usd        TEXT,
    brokerage_per_share_usd  TEXT NOT NULL DEFAULT '0',
    brokerage_flat_usd       TEXT NOT NULL DEFAULT '0',
    brokerage_gst_pct        TEXT NOT NULL DEFAULT '0',
    sell_fee_usd             TEXT NOT NULL DEFAULT '0',
    transfer_fee_inr         TEXT NOT NULL DEFAULT '0',
    expected_markup_pct      TEXT NOT NULL DEFAULT '1',
    is_builtin               INTEGER NOT NULL DEFAULT 0,
    sort_order               INTEGER NOT NULL DEFAULT 100
);

-- rate_source: 'sbi' / 'icici' / 'hdfc' have automatic adapters; 'manual' banks use typed-in rates.
CREATE TABLE banks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    rate_source TEXT NOT NULL DEFAULT 'manual',
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 100
);

-- One published TT rate per bank per day (auto-fetched or typed in).
CREATE TABLE bank_rates (
    bank_id      TEXT NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
    rate_date    TEXT NOT NULL,          -- YYYY-MM-DD (IST)
    published_at TEXT NOT NULL,
    tt_buy       TEXT NOT NULL,          -- bank buys $ from you (your withdrawals)
    tt_sell      TEXT NOT NULL,          -- bank sells $ to you (your deposits)
    origin       TEXT NOT NULL CHECK (origin IN ('auto', 'manual')),
    PRIMARY KEY (bank_id, rate_date)
);

-- Cache of mid-market bars from external sources.
CREATE TABLE fx_bars (
    source   TEXT NOT NULL,              -- 'yahoo' | 'frankfurter' | 'open_er'
    interval TEXT NOT NULL,              -- '1m' | '5m' | '1h' | '1d'
    ts       INTEGER NOT NULL,           -- bar start (daily sources: the requested day), unix seconds
    close    TEXT NOT NULL,
    fix_ts   INTEGER,                    -- daily sources: day the rate was actually fixed
    PRIMARY KEY (source, interval, ts)
);

-- Throttling for sources that should be fetched rarely (SBI archive, ICICI page).
CREATE TABLE fetch_log (
    source     TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    message    TEXT
);

CREATE TABLE forex_entries (
    id                INTEGER PRIMARY KEY,
    kind              TEXT NOT NULL CHECK (kind IN ('deposit', 'withdrawal', 'dividend')),
    occurred_at       TEXT NOT NULL,
    platform_id       TEXT NOT NULL REFERENCES platforms(id),
    bank_id           TEXT REFERENCES banks(id) ON DELETE SET NULL,
    inr_amount        TEXT,              -- deposit: ₹ debited; withdrawal: ₹ received
    usd_amount        TEXT NOT NULL,     -- deposit: $ credited; withdrawal: $ withdrawn; dividend: net $
    charges_inr       TEXT NOT NULL DEFAULT '0',
    tcs_inr           TEXT NOT NULL DEFAULT '0',
    ticker            TEXT,              -- dividends
    gross_usd         TEXT,              -- dividends
    withheld_usd      TEXT,              -- dividends
    market_rate       TEXT,              -- mid-market benchmark at occurred_at
    market_source     TEXT,              -- 'yahoo' | 'frankfurter' | 'open_er' | 'manual'
    market_interval   TEXT,              -- '1m' | '5m' | '1h' | '1d'
    market_bar_at     TEXT,
    market_stale     INTEGER NOT NULL DEFAULT 0,
    bank_rate         TEXT,              -- the bank's comparable TT rate (sell for deposits, buy for withdrawals)
    bank_rate_date    TEXT,
    bank_rate_manual  INTEGER NOT NULL DEFAULT 0,
    note              TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX forex_entries_occurred ON forex_entries (occurred_at);

CREATE TABLE stock_trades (
    id                 INTEGER PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('buy', 'sell', 'split', 'adjustment')),
    occurred_at        TEXT NOT NULL,
    platform_id        TEXT NOT NULL REFERENCES platforms(id),
    ticker             TEXT NOT NULL,
    quantity           TEXT,             -- buy / sell
    price_usd          TEXT,             -- buy / sell
    brokerage_usd      TEXT NOT NULL DEFAULT '0',
    brokerage_gst_usd  TEXT NOT NULL DEFAULT '0',
    other_fees_usd     TEXT NOT NULL DEFAULT '0',
    split_from         INTEGER,          -- split: e.g. 1 → 10
    split_to           INTEGER,
    qty_change         TEXT,             -- adjustment
    cost_change_usd    TEXT,             -- adjustment
    note               TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX stock_trades_occurred ON stock_trades (occurred_at);
CREATE INDEX stock_trades_ticker ON stock_trades (platform_id, ticker);

-- Built-in reference data. Fee defaults only pre-fill forms; users edit them in Settings.
INSERT INTO platforms (id, name, kind, brokerage_pct, brokerage_max_usd, brokerage_gst_pct,
                       expected_markup_pct, is_builtin, sort_order) VALUES
    ('indmoney', 'INDmoney', 'gift_city', '0.25', '25', '18', '1', 1, 1),
    ('vested', 'Vested', 'us_broker', '0', NULL, '18', '1', 1, 2);
INSERT INTO platforms (id, name, kind, brokerage_per_share_usd, brokerage_min_usd,
                       expected_markup_pct, is_builtin, sort_order) VALUES
    ('ibkr', 'Interactive Brokers', 'us_broker', '0.0035', '0.35', '1', 1, 3);
INSERT INTO platforms (id, name, kind, brokerage_gst_pct, expected_markup_pct, is_builtin,
                       sort_order) VALUES
    ('icici_direct', 'ICICI Direct Global', 'us_broker', '18', '1.5', 1, 4),
    ('bank_lrs', 'Direct via bank (LRS)', 'bank_lrs', '0', '1.5', 1, 5);

INSERT INTO banks (id, name, rate_source, is_builtin, sort_order) VALUES
    ('sbi', 'SBI', 'sbi', 1, 1),
    ('icici', 'ICICI Bank', 'icici', 1, 2),
    ('hdfc', 'HDFC Bank', 'hdfc', 1, 3),
    ('axis', 'Axis Bank', 'manual', 1, 4),
    ('kotak', 'Kotak Mahindra Bank', 'manual', 1, 5);

INSERT INTO settings (key, value) VALUES
    ('default_platform', 'indmoney'),
    ('default_bank', 'sbi'),
    ('backup_dir', NULL);
