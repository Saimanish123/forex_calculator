import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { EmptyState, MoneyInput, Segmented, Select, Spinner, toast } from "../components/ui.js";
import { formatDate, formatTime, inr, rate, signedInr, usd } from "../format.js";
import { html } from "../html.js";
import { platformName, useMeta } from "../meta.js";
import { TradeDrawer, price, shares, signedShares, signedUsd, tone } from "./trade-drawer.js";

const isTyping = (event) => ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);
const KIND_LABEL = { buy: "Buy", sell: "Sell", split: "Split", adjustment: "Adjustment" };
const FILTERS = {
  all: () => true,
  buy: (t) => t.kind === "buy",
  sell: (t) => t.kind === "sell",
  corporate: (t) => t.kind === "split" || t.kind === "adjustment",
};

const dayMonth = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });

export function StocksPage({ params }) {
  const meta = useMeta();
  const [holdings, setHoldings] = useState(null);
  const [holdingsError, setHoldingsError] = useState(null);
  const [trades, setTrades] = useState(null);
  const [tradesError, setTradesError] = useState(null);
  const [kind, setKind] = useState("all");
  const [platform, setPlatform] = useState("all");
  const [editing, setEditing] = useState(null); // null | "new" | trade

  // Holdings and trades load in parallel; every change reloads both.
  const load = useCallback(() => {
    api
      .get("/api/holdings")
      .then((d) => (setHoldings(d.platforms), setHoldingsError(null)))
      .catch((e) => setHoldingsError(e.message));
    api
      .get("/api/stock-trades")
      .then((d) => (setTrades(d.trades), setTradesError(null)))
      .catch((e) => setTradesError(e.message));
  }, []);
  useEffect(() => void load(), [load]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key.toLowerCase() === "t" && !editing && !isTyping(event) && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        setEditing("new");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);

  const list = trades ?? [];
  const usedPlatforms = useMemo(() => [...new Set(list.map((t) => t.platform_id))], [list]);
  const tickers = useMemo(
    () => [...new Set(list.flatMap((t) => (t.to_ticker ? [t.ticker, t.to_ticker] : [t.ticker])))].sort(),
    [list],
  );
  const visible = list.filter((t) => FILTERS[kind](t) && (platform === "all" || t.platform_id === platform));
  const close = useCallback(() => setEditing(null), []);
  const openNew = () => setEditing("new");

  return html`<div class="page">
    <header class="page-header">
      <div>
        <h1>Stocks</h1>
        <p>Your US stock trades and what each holding cost you in dollars and rupees.</p>
      </div>
      <button class="btn btn-primary" onClick=${openNew}>
        <${Icon} name="plus" />New trade<span class="kbd">T</span>
      </button>
    </header>

    <h2 class="section-title stock-first-title">Holdings</h2>
    <p class="section-sub">
      Shares you hold now. Sales use the oldest shares first (FIFO). Rupee cost is what the dollars behind each holding
      cost you, including conversion markup and charges.
    </p>
    <${Holdings} platforms=${holdings} error=${holdingsError} onChanged=${load} />

    <h2 class="section-title">Trades</h2>
    ${tradesError && html`<div class="callout error" style="margin-bottom:12px"><${Icon} name="alert" />${tradesError}</div>`}
    ${!trades && !tradesError && html`<${TableSkeleton} columns=${9} />`}
    ${trades &&
    (list.length === 0
      ? html`<div class="card">
          <${EmptyState}
            icon="layers"
            title="No trades yet"
            action=${html`<button class="btn btn-primary" onClick=${openNew}>
              <${Icon} name="plus" />Record your first trade
            </button>`}
          >
            Add your buys and sells from your ${platformName(meta, meta.settings.default_platform)} contract notes. The app
            tracks each lot's dollar and rupee cost, oldest shares first.
          </${EmptyState}>
        </div>`
      : html`
          <div class="toolbar">
            <${Segmented}
              value=${kind}
              onChange=${setKind}
              options=${[
                { value: "all", label: "All" },
                { value: "buy", label: "Buys" },
                { value: "sell", label: "Sells" },
                { value: "corporate", label: "Corporate actions" },
              ]}
            />
            ${usedPlatforms.length > 1 &&
            html`<div class="stock-filter-select">
              <${Select}
                value=${platform}
                onChange=${setPlatform}
                aria-label="Platform"
                options=${[
                  { value: "all", label: "All platforms" },
                  ...usedPlatforms.map((id) => ({ value: id, label: platformName(meta, id) })),
                ]}
              />
            </div>`}
          </div>
          <div class="card table-wrap">
            <${TradesTable} trades=${visible} meta=${meta} onOpen=${setEditing} />
            ${visible.length === 0 &&
            html`<div class="empty" style="padding:32px"><p style="margin:0">No trades match these filters.</p></div>`}
          </div>
        `)}

    ${editing &&
    html`<${TradeDrawer}
      trade=${editing === "new" ? null : editing}
      holdings=${holdings}
      tickers=${tickers}
      onClose=${close}
      onSaved=${() => (close(), load())}
    />`}
  </div>`;
}

// ---- holdings ----------------------------------------------------------------------------

function Holdings({ platforms, error, onChanged }) {
  const [open, setOpen] = useState(() => new Set());
  if (error) return html`<div class="callout error"><${Icon} name="alert" />${error}</div>`;
  if (!platforms) return html`<${TableSkeleton} columns=${9} rows=${3} />`;

  const withShares = platforms.filter((p) => p.holdings.length > 0);
  const withCash = platforms.filter((p) => p.holdings.length === 0 && Number(p.cash.usd) > 0);
  if (withShares.length === 0 && withCash.length === 0) {
    return html`<div class="card">
      <${EmptyState} icon="layers" title="No holdings">
        Shares you buy appear here with their dollar and rupee cost, current value and individual lots.
      </${EmptyState}>
    </div>`;
  }
  const headings = withShares.length + withCash.length > 1;
  const toggle = (key) =>
    setOpen((current) => {
      const next = new Set(current);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  return html`<div class="stack">
    ${withShares.map(
      (p) => html`<section key=${p.platform_id}>
        ${headings && html`<h3 class="stock-platform-title">${p.name}</h3>`}
        <div class="card">
          <div class="table-wrap">
            <table class="stock-holdings">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th class="r">Shares</th>
                  <th class="r">Avg cost / share<div class="stock-th-sub">excl. fees</div></th>
                  <th class="r">Cost</th>
                  <th class="r">Rupee cost</th>
                  <th class="r">Price</th>
                  <th class="r">Market value</th>
                  <th class="r">Unrealized<div class="stock-th-sub">before sell fees</div></th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${p.holdings.map((h) => {
                  const key = `${p.platform_id}:${h.ticker}`;
                  return html`<${HoldingRow}
                    key=${key}
                    platformId=${p.platform_id}
                    holding=${h}
                    expanded=${open.has(key)}
                    onToggle=${() => toggle(key)}
                    onChanged=${onChanged}
                  />`;
                })}
              </tbody>
            </table>
          </div>
          <${CashLine} cash=${p.cash} />
        </div>
      </section>`,
    )}
    ${withCash.map(
      (p) => html`<section key=${p.platform_id}>
        ${headings && html`<h3 class="stock-platform-title">${p.name}</h3>`}
        <div class="card">
          <div class="stock-no-shares">No shares held on ${p.name}.</div>
          <${CashLine} cash=${p.cash} />
        </div>
      </section>`,
    )}
  </div>`;
}

function CashLine({ cash }) {
  if (!(Number(cash.usd) > 0)) return null;
  return html`<div class="stock-cash">
    <${Icon} name="coins" size=${14} />
    <span>
      Uninvested cash <strong>${usd(cash.usd)}</strong>
      <span class="muted"> · ${inr(cash.inr_cost)} cost${cash.avg_inr_per_usd ? ` (${rate(cash.avg_inr_per_usd, 4)} per $)` : ""}</span>
    </span>
  </div>`;
}

function HoldingRow({ platformId, holding: h, expanded, onToggle, onChanged }) {
  const sellHref = `#/calculator?${new URLSearchParams({ platform: platformId, ticker: h.ticker, quantity: h.quantity })}`;
  return html`
    <tr class=${expanded ? "stock-row-open" : ""}>
      <td class="nowrap">
        <button
          type="button"
          class=${`stock-ticker ${expanded ? "open" : ""}`}
          onClick=${onToggle}
          aria-expanded=${expanded}
          title=${expanded ? "Hide lots" : "Show lots"}
        >
          <${Icon} name="chevron" size=${14} /><span>${h.ticker}</span>
        </button>
        <div class="cell-sub stock-indent">since ${formatDate(h.first_acquired)}</div>
      </td>
      <td class="r">${shares(h.quantity)}</td>
      <td class="r">${price(h.avg_price_usd)}</td>
      <td class="r">${usd(h.cost_usd)}</td>
      <td class="r nowrap">
        ${inr(h.inr_cost)}
        ${h.avg_inr_per_usd && html`<div class="cell-sub">${rate(h.avg_inr_per_usd, 4)} per $</div>`}
      </td>
      <td class="r"><${PriceCell} holding=${h} onChanged=${onChanged} /></td>
      <td class="r">${h.market_value_usd == null ? html`<span class="subtle">—</span>` : usd(h.market_value_usd)}</td>
      <td class="r nowrap">
        ${h.unrealized_usd == null
          ? html`<span class="subtle">—</span>`
          : html`<span class=${tone(h.unrealized_usd)}>${signedUsd(h.unrealized_usd)}</span>`}
      </td>
      <td class="r"><a class="btn btn-sm" href=${sellHref} title="Open the sell calculator for all these shares">Sell…</a></td>
    </tr>
    ${expanded &&
    html`<tr class="stock-lots-row">
      <td colspan="9">
        <div class="stock-lots">
          <div class="stock-lots-title">Lots · oldest first, sold first</div>
          <table>
            <thead>
              <tr>
                <th>Acquired</th>
                <th class="r">Shares</th>
                <th class="r">Cost</th>
                <th class="r">Rupee cost</th>
              </tr>
            </thead>
            <tbody>
              ${h.lots.map(
                (lot, i) => html`<tr key=${`${lot.trade_id}-${i}`}>
                  <td class="nowrap">${formatDate(lot.acquired_at)}<span class="muted"> · ${formatTime(lot.acquired_at)} IST</span></td>
                  <td class="r">${shares(lot.quantity)}</td>
                  <td class="r">${usd(lot.cost_usd)}</td>
                  <td class="r">${inr(lot.inr_cost)}</td>
                </tr>`,
              )}
            </tbody>
          </table>
        </div>
      </td>
    </tr>`}
  `;
}

function priceOrigin(p) {
  if (!p) return "no price yet";
  if (p.origin === "manual") return "entered by you";
  return p.as_of ? `fetched ${dayMonth.format(new Date(p.as_of))}, ${formatTime(p.as_of)}` : "fetched";
}

function PriceCell({ holding, onChanged }) {
  const current = holding.price;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const done = useRef(false);
  const initial = useRef("");

  const start = () => {
    done.current = false;
    initial.current = current ? String(current.price_usd).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1") : "";
    setValue(initial.current);
    setEditing(true);
  };
  const cancel = () => {
    done.current = true;
    setEditing(false);
  };
  // Reads the typed text from the input itself, so Enter right after typing saves what's shown.
  const commit = async (typed) => {
    if (done.current) return;
    done.current = true;
    if (!(Number(typed) > 0) || typed === initial.current) return setEditing(false);
    setBusy(true);
    try {
      const saved = await api.put(`/api/prices/${encodeURIComponent(holding.ticker)}`, { price_usd: typed });
      toast(`${saved.ticker} price set to ${price(saved.price_usd)}`);
      setEditing(false);
      onChanged();
    } catch (e) {
      toast(e.message, "error");
      done.current = false;
    } finally {
      setBusy(false);
    }
  };
  const fetchLatest = async () => {
    setBusy(true);
    try {
      const saved = await api.post(`/api/prices/${encodeURIComponent(holding.ticker)}/fetch`);
      toast(`${saved.ticker} price updated to ${price(saved.price_usd)}`);
      onChanged();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return html`<div class="stock-price-edit">
      <${MoneyInput}
        prefix="$"
        value=${value}
        onInput=${setValue}
        decimals=${4}
        autofocus
        aria-label=${`Price of ${holding.ticker}`}
        disabled=${busy}
        onKeyDown=${(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(e.target.value);
          } else if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            cancel();
          }
        }}
        onBlur=${(e) => commit(e.target.value)}
      />
      <div class="cell-sub">Enter to save · Esc to cancel</div>
    </div>`;
  }

  return html`<div class="stock-price">
    <div class="stock-price-line">
      <button type="button" class="stock-price-value" onClick=${start} title="Click to enter a price">
        ${current ? price(current.price_usd) : "Set price"}
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-icon stock-fetch"
        onClick=${fetchLatest}
        disabled=${busy}
        title="Fetch latest price from Yahoo Finance"
        aria-label=${`Fetch latest price for ${holding.ticker}`}
      >
        ${busy ? html`<${Spinner} size=${13} />` : html`<${Icon} name="refresh" size=${13} />`}
      </button>
    </div>
    <div class="cell-sub">${priceOrigin(current)}</div>
  </div>`;
}

// ---- trades ------------------------------------------------------------------------------

function sharesCell(t) {
  if (t.kind === "split") return `${t.split_to}-for-${t.split_from}`;
  if (t.kind === "adjustment") return t.to_ticker ? `${shares(t.qty_change)} → ${t.to_ticker}` : signedShares(t.qty_change);
  return shares(t.quantity);
}

function TradesTable({ trades, meta, onOpen }) {
  return html`<table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Type</th>
        <th>Platform</th>
        <th>Ticker</th>
        <th class="r">Shares</th>
        <th class="r">Price</th>
        <th class="r">Fees</th>
        <th class="r">Total</th>
        <th class="r">Realized</th>
      </tr>
    </thead>
    <tbody>
      ${trades.map((t) => {
        const trading = t.kind === "buy" || t.kind === "sell";
        const r = t.realized;
        return html`<tr
          class="clickable"
          key=${t.id}
          tabindex="0"
          onClick=${() => onOpen(t)}
          onKeyDown=${(ev) => ev.key === "Enter" && onOpen(t)}
        >
          <td class="nowrap">${formatDate(t.occurred_at)}<div class="cell-sub">${formatTime(t.occurred_at)} IST</div></td>
          <td><span class=${`badge ${t.kind === "buy" || t.kind === "sell" ? "" : "solid"}`}>${KIND_LABEL[t.kind]}</span></td>
          <td>${platformName(meta, t.platform_id)}</td>
          <td class="nowrap">
            <span class="stock-symbol">${t.ticker}</span>
            ${t.note && html`<div class="cell-sub stock-note" title=${t.note}>${t.note}</div>`}
          </td>
          <td class="r nowrap">${sharesCell(t)}</td>
          <td class="r">${trading ? price(t.price_usd) : html`<span class="subtle">—</span>`}</td>
          <td class="r">${trading ? usd(t.fees_usd) : html`<span class="subtle">—</span>`}</td>
          <td class="r nowrap">
            ${trading
              ? html`${usd(t.total_usd)}<div class="cell-sub">${t.kind === "buy" ? "debited" : "credited"}</div>`
              : html`<span class="subtle">—</span>`}
          </td>
          <td class="r nowrap">
            ${r
              ? html`<span class=${tone(r.gain_usd)}>${signedUsd(r.gain_usd)}</span>
                  <div class=${`cell-sub ${tone(r.gain_inr)}`}>${signedInr(r.gain_inr)}</div>`
              : html`<span class="subtle">—</span>`}
          </td>
        </tr>`;
      })}
    </tbody>
  </table>`;
}

function TableSkeleton({ columns, rows = 4 }) {
  return html`<div class="card table-wrap">
    <table>
      <thead>
        <tr>
          ${Array.from({ length: columns }, () => html`<th><div class="skeleton" style="height:12px;width:56px" /></th>`)}
        </tr>
      </thead>
      <tbody>
        ${Array.from(
          { length: rows },
          () => html`<tr><td colspan=${columns}><div class="skeleton" style="height:16px;width:100%" /></td></tr>`,
        )}
      </tbody>
    </table>
  </div>`;
}
