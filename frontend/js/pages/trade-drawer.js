import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { Disclosure, Drawer, Field, MoneyInput, Segmented, Select, Spinner, toast } from "../components/ui.js";
import { fromIstInput, inr, isNegative, isZero, nowIstInput, rate, signedInr, toIstInput, usd } from "../format.js";
import { html } from "../html.js";
import { platformName, useMeta } from "../meta.js";

// ---- display helpers shared with the Stocks page (formatting only, no arithmetic) -----------

const MINUS = "−";
const shareFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });

// Share quantities as stored (up to 8 decimals); Intl formats the decimal string exactly.
export function shares(value) {
  if (value === null || value === undefined || value === "") return "—";
  const text = String(value);
  return text.startsWith("-") ? `${MINUS}${shareFmt.format(text.slice(1))}` : shareFmt.format(text);
}

export const signedShares = (value) => (isNegative(value) ? shares(value) : `+${shares(value)}`);

// Per-share prices and US fees carry up to 4 decimals: show 2, or more only when they're significant.
export function price(value) {
  if (value === null || value === undefined || value === "") return "—";
  const fraction = (String(value).split(".")[1] || "").replace(/0+$/, "");
  return usd(value, Math.min(4, Math.max(2, fraction.length)));
}

export function signedUsd(value) {
  if (value === null || value === undefined) return "—";
  return isNegative(value) || isZero(value) ? usd(value) : `+${usd(value)}`;
}

export const tone = (value) => (value == null || isZero(value) ? "" : isNegative(value) ? "neg" : "pos");

// ---- percent <-> fraction as strings (moving the decimal point, never floating-point math) ---

const trimZeros = (whole, fraction) => {
  const w = whole.replace(/^0+(?=\d)/, "") || "0";
  const f = fraction.replace(/0+$/, "");
  return f ? `${w}.${f}` : w;
};

export function percentToFraction(percent) {
  const [whole = "", fraction = ""] = String(percent).split(".");
  const padded = (whole || "0").padStart(3, "0");
  return trimZeros(padded.slice(0, -2), padded.slice(-2) + fraction);
}

export function fractionToPercent(value) {
  const [whole = "", fraction = ""] = String(value).split(".");
  const digits = fraction.padEnd(2, "0");
  return trimZeros((whole || "0") + digits.slice(0, 2), digits.slice(2));
}

// ---- form ------------------------------------------------------------------------------------

const positive = (value) => value !== "" && Number(value) > 0;
const integer = (value) => /^\d+$/.test(value) && Number(value) > 0;

function initialForm(trade, settings) {
  const form = {
    kind: "buy",
    platform_id: settings.default_platform,
    occurred: nowIstInput(),
    ticker: "",
    quantity: "",
    price: "",
    brokerage: "",
    gst: "",
    other: "",
    split_to: "",
    split_from: "1",
    adj_mode: "quantity",
    adj_direction: "add",
    adj_quantity: "",
    to_ticker: "",
    received: "",
    cost_pct: "100",
    cancel_source: true,
    market_rate: "",
    note: "",
  };
  if (!trade) return form;
  Object.assign(form, {
    kind: trade.kind,
    platform_id: trade.platform_id,
    occurred: toIstInput(trade.occurred_at),
    ticker: trade.ticker,
    market_rate: trade.market?.source === "manual" ? trade.market.rate : "",
    note: trade.note || "",
  });
  if (trade.kind === "buy" || trade.kind === "sell") {
    Object.assign(form, {
      quantity: trade.quantity,
      price: trade.price_usd,
      brokerage: trade.brokerage_usd,
      gst: trade.brokerage_gst_usd,
      other: trade.other_fees_usd,
    });
  } else if (trade.kind === "split") {
    Object.assign(form, { split_to: String(trade.split_to), split_from: String(trade.split_from) });
  } else if (trade.to_ticker) {
    Object.assign(form, {
      adj_mode: "move",
      to_ticker: trade.to_ticker,
      received: trade.qty_change,
      cost_pct: fractionToPercent(trade.cost_fraction),
      cancel_source: trade.cancel_source,
    });
  } else {
    const removed = isNegative(trade.qty_change);
    Object.assign(form, {
      adj_mode: "quantity",
      adj_direction: removed ? "remove" : "add",
      adj_quantity: removed ? trade.qty_change.slice(1) : trade.qty_change,
    });
  }
  return form;
}

function payload(form) {
  const body = {
    kind: form.kind,
    occurred_at: fromIstInput(form.occurred),
    platform_id: form.platform_id,
    ticker: form.ticker.trim(),
    market_rate_override: form.kind !== "split" && positive(form.market_rate) ? form.market_rate : null,
    note: form.note.trim() || null,
  };
  if (form.kind === "buy" || form.kind === "sell") {
    return {
      ...body,
      quantity: form.quantity,
      price_usd: form.price,
      brokerage_usd: form.brokerage || "0",
      brokerage_gst_usd: form.gst || "0",
      other_fees_usd: form.other || "0",
    };
  }
  if (form.kind === "split") return { ...body, split_to: Number(form.split_to), split_from: Number(form.split_from) };
  if (form.adj_mode === "move") {
    return {
      ...body,
      to_ticker: form.to_ticker.trim(),
      qty_change: form.received,
      cost_fraction: percentToFraction(form.cancel_source ? "100" : form.cost_pct),
      cancel_source: form.cancel_source,
    };
  }
  return { ...body, qty_change: `${form.adj_direction === "remove" ? "-" : ""}${form.adj_quantity}` };
}

function isReady(form) {
  if (!form.platform_id || !form.occurred || !form.ticker.trim()) return false;
  if (form.kind === "buy" || form.kind === "sell") return positive(form.quantity) && positive(form.price);
  if (form.kind === "split") return integer(form.split_to) && integer(form.split_from) && form.split_to !== form.split_from;
  if (form.adj_mode === "move") {
    const pctOk = form.cancel_source || (positive(form.cost_pct) && Number(form.cost_pct) <= 100);
    return Boolean(form.to_ticker.trim()) && positive(form.received) && pctOk;
  }
  return positive(form.adj_quantity);
}

const tickerInput = (value, onInput, rest = {}) => html`<input
  class="input stock-ticker-input"
  value=${value}
  maxlength="15"
  autocomplete="off"
  autocapitalize="characters"
  spellcheck="false"
  placeholder="e.g. AAPL"
  onInput=${(e) => {
    const upper = e.target.value.toUpperCase();
    if (upper !== e.target.value) e.target.value = upper;
    onInput(upper);
  }}
  ...${rest}
/>`;

function IntegerInput({ value, onInput, ...rest }) {
  return html`<input
    class="input num"
    inputmode="numeric"
    autocomplete="off"
    value=${value}
    onInput=${(e) => {
      const digits = e.target.value.replace(/\D/g, "").slice(0, 7);
      if (digits !== e.target.value) e.target.value = digits;
      onInput(digits);
    }}
    ...${rest}
  />`;
}

export function TradeDrawer({ trade, holdings, tickers, onClose, onSaved }) {
  const meta = useMeta();
  const [form, setForm] = useState(() => initialForm(trade, meta.settings));
  // Saved trades keep their own fee figures; new trades start from the platform's fee estimate.
  const [feesEdited, setFeesEdited] = useState(Boolean(trade));
  const [estimating, setEstimating] = useState(false);
  const [estimateError, setEstimateError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(Boolean(form.market_rate || form.note));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const requestId = useRef(0);
  const previewId = useRef(0);

  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));
  const setFee = (key) => (value) => {
    setFeesEdited(true);
    setForm((f) => ({ ...f, [key]: value }));
  };

  const trading = form.kind === "buy" || form.kind === "sell";
  const sell = form.kind === "sell";
  const ready = isReady(form);
  const platform = platformName(meta, form.platform_id);
  const ticker = form.ticker.trim();

  // Fee estimate from the platform's rule, until the user types their own figures.
  useEffect(() => {
    if (!trading || feesEdited) {
      setEstimating(false);
      return;
    }
    const id = ++requestId.current;
    if (!positive(form.quantity) || !positive(form.price) || !form.platform_id) {
      setEstimateError(null);
      setEstimating(false);
      setForm((f) => ({ ...f, brokerage: "", gst: "", other: "" }));
      return;
    }
    setEstimating(true);
    const timer = setTimeout(() => {
      api
        .post("/api/stock-trades/fees", {
          platform_id: form.platform_id,
          side: form.kind,
          quantity: form.quantity,
          price_usd: form.price,
        })
        .then((fees) => {
          if (id !== requestId.current) return;
          setEstimateError(null);
          setForm((f) => ({ ...f, brokerage: fees.brokerage_usd, gst: fees.brokerage_gst_usd, other: fees.other_fees_usd }));
        })
        .catch((e) => id === requestId.current && setEstimateError(e.message))
        .finally(() => id === requestId.current && setEstimating(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [form.kind, form.platform_id, form.quantity, form.price, feesEdited, trading]);

  // Exact totals and realized results come from the server, which applies the trade to the whole
  // ledger in a transaction it always rolls back.
  const body = ready ? JSON.stringify(payload(form)) : null;
  useEffect(() => {
    if (!body || estimating) {
      if (!body) (setPreview(null), setPreviewError(null));
      return;
    }
    const id = ++previewId.current;
    setPreviewing(true);
    const timer = setTimeout(() => {
      api
        .post(`/api/stock-trades/preview${trade ? `?trade_id=${trade.id}` : ""}`, JSON.parse(body))
        .then((result) => id === previewId.current && (setPreview(result), setPreviewError(null)))
        .catch((e) => id === previewId.current && (setPreview(null), setPreviewError(e.message)))
        .finally(() => id === previewId.current && setPreviewing(false));
    }, 350);
    return () => clearTimeout(timer);
  }, [body, estimating]);

  const held = holdings
    ?.find((p) => p.platform_id === form.platform_id)
    ?.holdings.find((h) => h.ticker === ticker);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (trade) await api.put(`/api/stock-trades/${trade.id}`, payload(form));
      else await api.post("/api/stock-trades", payload(form));
      toast(trade ? "Trade updated" : "Trade saved");
      onSaved();
    } catch (e) {
      setError(e.message);
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 3000);
      return;
    }
    try {
      await api.del(`/api/stock-trades/${trade.id}`);
      toast("Trade deleted");
      onSaved();
    } catch (e) {
      setConfirmDelete(false);
      setError(e.message);
    }
  };

  const footer = html`
    ${trade &&
    html`<button class="btn btn-ghost btn-danger btn-sm" onClick=${remove}>
      <${Icon} name="trash" size=${14} />${confirmDelete ? "Click again to delete" : "Delete"}
    </button>`}
    <span class="spacer" />
    <button class="btn" onClick=${onClose}>Cancel</button>
    <button class="btn btn-primary" onClick=${save} disabled=${!ready || saving || Boolean(previewError)}>
      ${saving ? html`<${Spinner} />` : null}${trade ? "Save changes" : "Save trade"}
    </button>
  `;

  let heldHint = null;
  if (sell && ticker) {
    if (trade) heldHint = held ? `You hold ${shares(held.quantity)} ${ticker} shares on ${platform} now` : null;
    else heldHint = held ? `You hold ${shares(held.quantity)} ${ticker} shares on ${platform}` : `You don't hold ${ticker} on ${platform}`;
  }

  return html`<${Drawer} title=${trade ? "Edit trade" : "New trade"} onClose=${onClose} footer=${footer}>
    <div class="stack">
      <${Segmented}
        block
        value=${form.kind}
        onChange=${set("kind")}
        options=${[
          { value: "buy", label: "Buy" },
          { value: "sell", label: "Sell" },
          { value: "split", label: "Split" },
          { value: "adjustment", label: "Adjustment" },
        ]}
      />

      <div class="row">
        <${Field} label="Platform">
          <${Select}
            value=${form.platform_id}
            onChange=${set("platform_id")}
            options=${meta.platforms.map((p) => ({ value: p.id, label: p.name }))}
          />
        </${Field}>
        <${Field}
          id="trade-occurred-at"
          label="Date & time (IST)"
          aside=${html`<button type="button" class="link field-hint" onClick=${() => set("occurred")(nowIstInput())}>Now</button>`}
        >
          <input
            id="trade-occurred-at"
            class="input"
            type="datetime-local"
            value=${form.occurred}
            max=${nowIstInput()}
            onInput=${(e) => set("occurred")(e.target.value)}
          />
        </${Field}>
      </div>

      <${Field} label="Ticker" hint=${form.kind === "adjustment" && form.adj_mode === "move" ? "The ticker you held before" : null}>
        ${tickerInput(form.ticker, set("ticker"), { list: "trade-tickers", autofocus: !trade })}
      </${Field}>
      <datalist id="trade-tickers">${(tickers || []).map((t) => html`<option value=${t} />`)}</datalist>

      ${trading &&
      html`
        <div class="row">
          <${Field}
            id="trade-quantity"
            label="Shares"
            hint=${heldHint}
            aside=${sell && held && !trade
              ? html`<button type="button" class="link field-hint" onClick=${() => set("quantity")(held.quantity)}>Sell all</button>`
              : null}
          >
            <${MoneyInput} id="trade-quantity" value=${form.quantity} onInput=${set("quantity")} decimals=${8} placeholder="0" />
          </${Field}>
          <${Field} label="Price per share">
            <${MoneyInput} prefix="$" value=${form.price} onInput=${set("price")} decimals=${4} />
          </${Field}>
        </div>
        <div>
          <div class="stock-fee-grid">
            <${Field} label="Brokerage">
              <${MoneyInput} prefix="$" value=${form.brokerage} onInput=${setFee("brokerage")} decimals=${4} />
            </${Field}>
            <${Field} label="GST on brokerage">
              <${MoneyInput} prefix="$" value=${form.gst} onInput=${setFee("gst")} decimals=${4} />
            </${Field}>
            <${Field} label="Other fees">
              <${MoneyInput} prefix="$" value=${form.other} onInput=${setFee("other")} decimals=${4} />
            </${Field}>
          </div>
          <p class="field-hint stock-fee-hint">
            ${feesEdited
              ? html`Edited — using your figures.
                  <button type="button" class="link" onClick=${() => setFeesEdited(false)}>Reset to estimate</button>`
              : estimateError
                ? html`<span class="field-error">Couldn't estimate fees: ${estimateError}</span>`
                : `Estimated from ${platform}${platform.endsWith("s") ? "'" : "'s"} fee rule. Replace with your contract note figures.`}
          </p>
        </div>
      `}

      ${form.kind === "split" &&
      html`
        <div class="row">
          <${Field} label="New shares">
            <${IntegerInput} value=${form.split_to} onInput=${set("split_to")} placeholder="e.g. 10" />
          </${Field}>
          <${Field} label="For every old shares">
            <${IntegerInput} value=${form.split_from} onInput=${set("split_from")} placeholder="1" />
          </${Field}>
        </div>
        <${SplitPreview} form=${form} />
      `}

      ${form.kind === "adjustment" && html`<${AdjustmentFields} form=${form} set=${set} setForm=${setForm} />`}

      <div>
        <${Disclosure} label="Advanced" open=${showAdvanced} onToggle=${() => setShowAdvanced(!showAdvanced)} />
        ${showAdvanced &&
        html`<div class="stack" style="margin-top:12px">
          ${form.kind !== "split" &&
          html`<${Field}
            label="Market rate override"
            hint="USD/INR used to value this trade in rupees. Leave empty to use the market rate for this exact minute automatically"
          >
            <${MoneyInput} prefix="₹" value=${form.market_rate} onInput=${set("market_rate")} decimals=${4} placeholder="Automatic" />
          </${Field}>`}
          <${Field} label="Note">
            <textarea class="textarea" maxlength="500" value=${form.note} onInput=${(e) => set("note")(e.target.value)} />
          </${Field}>
        </div>`}
      </div>

      ${previewError
        ? html`<div class="callout error"><${Icon} name="alert" />${previewError}</div>`
        : trading
          ? html`<${TradeSummary} kind=${form.kind} preview=${ready ? preview : null} loading=${previewing || estimating} feesEdited=${feesEdited} />`
          : preview?.realized && html`<${RealizedPanel} realized=${preview.realized} />`}
      ${error && html`<div class="callout error"><${Icon} name="alert" />${error}</div>`}
    </div>
  </${Drawer}>`;
}

function SplitPreview({ form }) {
  if (!integer(form.split_to) || !integer(form.split_from)) {
    return html`<div class="callout"><${Icon} name="info" />Enter the ratio from the company's announcement, e.g. 10 new shares for every 1 old share.</div>`;
  }
  if (form.split_to === form.split_from) {
    return html`<div class="callout error"><${Icon} name="alert" />A split changes the number of shares; the ratio can't be equal.</div>`;
  }
  const one = form.split_from === "1";
  const what = form.ticker.trim() ? `${form.ticker.trim()} share` : "share";
  return html`<div class="callout">
    <${Icon} name="split" />
    <span>
      Every ${form.split_from} ${what}${one ? "" : "s"} become${one ? "s" : ""} ${form.split_to}.
      Your total cost stays the same; the cost per share changes. Applies to all shares held on this platform at that time.
    </span>
  </div>`;
}

function AdjustmentFields({ form, set, setForm }) {
  const move = form.adj_mode === "move";
  return html`
    <${Segmented}
      block
      value=${form.adj_mode}
      onChange=${set("adj_mode")}
      options=${[
        { value: "quantity", label: "Shares added or removed" },
        { value: "move", label: "Moved to another ticker" },
      ]}
    />
    ${move
      ? html`
          <div class="row">
            <${Field} label="New ticker">${tickerInput(form.to_ticker, set("to_ticker"), { placeholder: "e.g. NEWT" })}</${Field}>
            <${Field} label="Shares received" hint="Of the new ticker">
              <${MoneyInput} value=${form.received} onInput=${set("received")} decimals=${8} placeholder="0" />
            </${Field}>
          </div>
          <label class="stock-check">
            <input
              type="checkbox"
              checked=${form.cancel_source}
              onChange=${(e) => {
                const checked = e.target.checked;
                setForm((f) => ({ ...f, cancel_source: checked, cost_pct: checked ? "100" : f.cost_pct }));
              }}
            />
            <span>
              Old shares stop existing (merger or symbol change)
              <span class="field-hint">All of the old shares' cost moves to the new ticker.</span>
            </span>
          </label>
          <${Field}
            label="Cost moved"
            hint=${form.cancel_source
              ? "100% because the old shares no longer exist"
              : "Share of the old shares' cost that moves to the new ticker, e.g. from the company's cost-basis allocation for a spin-off. The old shares keep the rest."}
          >
            <${MoneyInput}
              suffix="%"
              value=${form.cancel_source ? "100" : form.cost_pct}
              onInput=${set("cost_pct")}
              decimals=${4}
              placeholder="100"
              disabled=${form.cancel_source}
            />
          </${Field}>
        `
      : html`
          <div class="row">
            <${Field} label="Change">
              <${Select}
                value=${form.adj_direction}
                onChange=${set("adj_direction")}
                options=${[
                  { value: "add", label: "Shares received" },
                  { value: "remove", label: "Shares removed" },
                ]}
              />
            </${Field}>
            <${Field} label="Shares">
              <${MoneyInput} value=${form.adj_quantity} onInput=${set("adj_quantity")} decimals=${8} placeholder="0" />
            </${Field}>
          </div>
          <div class="callout">
            <${Icon} name="info" />
            <span>
              ${form.adj_direction === "add"
                ? "Received shares are added at zero cost, e.g. bonus shares. Your total cost stays the same."
                : "Removed shares are written off with no proceeds, e.g. after a delisting. Their cost is recorded as a realized loss, oldest shares first."}
            </span>
          </div>
        `}
  `;
}

// Money shown here comes from the server only (the rolled-back preview of this exact trade).
function TradeSummary({ kind, preview, loading, feesEdited }) {
  const buy = kind === "buy";
  const label = buy ? "Total debited" : "Total credited";
  if (!preview) {
    return html`<div class="callout"><${Icon} name="info" />Enter shares and price to see the exact ${buy ? "cost" : "proceeds"}.</div>`;
  }
  const r = preview.realized;
  return html`<section class="result" aria-live="polite">
    <div class="result-head">
      <span class="label">${label}</span>
      ${loading ? html`<span class="subtle"><${Spinner} size=${13} /></span>` : null}
    </div>
    <div class="result-big">${usd(preview.total_usd)}</div>
    <div class="result-lines">
      <div class="line"><span class="k">${shares(preview.quantity)} × ${price(preview.price_usd)}</span><span>${usd(preview.gross_usd)}</span></div>
      <div class="line"><span class="k">Fees${feesEdited ? "" : " (estimated)"}</span><span>${buy ? "+" : "−"}${usd(preview.fees_usd)}</span></div>
      <div class="line total"><span>${label}</span><span>${usd(preview.total_usd)}</span></div>
    </div>
    ${r &&
    html`<div class="result-lines">
      <div class="line"><span class="k">Cost of shares sold (oldest first)</span><span>${usd(r.cost_usd)}</span></div>
      <div class="line"><span class="k">Realized gain</span><span class=${tone(r.gain_usd)}>${signedUsd(r.gain_usd)}</span></div>
      <div class="line"><span class="k">Rupee cost of shares sold</span><span>${inr(r.inr_cost)}</span></div>
      <div class="line"><span class="k">Realized gain in rupees</span><span class=${tone(r.gain_inr)}>${signedInr(r.gain_inr)}</span></div>
    </div>`}
    ${preview.market &&
    html`<div class="result-note">
      <${Icon} name="info" size=${14} />
      <div>Valued at ${rate(preview.market.rate, 4)} per $${preview.market.source === "manual" ? " (entered by you)" : ", the market rate at the time of the trade"}.</div>
    </div>`}
  </section>`;
}

function RealizedPanel({ realized: r }) {
  return html`<section class="result">
    <div class="result-head"><span class="label">Written off</span></div>
    <div class="result-lines">
      <div class="line"><span class="k">Cost written off (oldest first)</span><span>${usd(r.cost_usd)}</span></div>
      <div class="line"><span class="k">Rupee cost written off</span><span>${inr(r.inr_cost)}</span></div>
      <div class="line total"><span>Realized in rupees</span><span class=${tone(r.gain_inr)}>${signedInr(r.gain_inr)}</span></div>
    </div>
  </section>`;
}
