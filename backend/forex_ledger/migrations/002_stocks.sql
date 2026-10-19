-- Phase 2/3: market rate on stock trades, corporate-action transfers, current prices, tax settings.

-- Mid-market rate at the trade's time: values realized gains and any trade not covered by
-- recorded deposits.
ALTER TABLE stock_trades ADD COLUMN market_rate TEXT;
ALTER TABLE stock_trades ADD COLUMN market_source TEXT;
ALTER TABLE stock_trades ADD COLUMN market_interval TEXT;
ALTER TABLE stock_trades ADD COLUMN market_bar_at TEXT;
ALTER TABLE stock_trades ADD COLUMN market_stale INTEGER NOT NULL DEFAULT 0;

-- Adjustment that moves cost basis to another ticker (merger, symbol change, spin-off):
-- `cost_fraction` of every open lot's cost moves to `to_ticker` (keeping acquisition dates) and
-- `qty_change` shares of `to_ticker` are received. `cancel_source` = 1 when the original shares
-- stop existing (merger / symbol change), 0 when they're kept (spin-off).
ALTER TABLE stock_trades ADD COLUMN to_ticker TEXT;
ALTER TABLE stock_trades ADD COLUMN cost_fraction TEXT;
ALTER TABLE stock_trades ADD COLUMN cancel_source INTEGER NOT NULL DEFAULT 0;

-- Latest known price per ticker (typed in or fetched), used by the calculator and dashboard.
CREATE TABLE ticker_prices (
    ticker     TEXT PRIMARY KEY,
    price_usd  TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    origin     TEXT NOT NULL CHECK (origin IN ('auto', 'manual'))
);

INSERT INTO settings (key, value) VALUES
    ('tax_slab_pct', '30'),
    ('estimate_conversion_gst', '1');
