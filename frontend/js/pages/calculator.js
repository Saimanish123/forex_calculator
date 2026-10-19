import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { Disclosure, EmptyState, Field, MoneyInput, Select, Spinner, Stat, toast } from "../components/ui.js";
import {
  formatDate,
  formatShort,
  inr,
  intervalLabel,
  isNegative,
  isZero,
  pct,
  rate,
  signedInr,
  sourceLabel,
  usd,
} from "../format.js";
import { html } from "../html.js";
import { platformName, useMeta } from "../meta.js";

// ---- display helpers shared with the dashboard (string logic only, never arithmetic) --------

const MINUS = "−";
const positive = (value) => value !== "" && value != null && Number(value) > 0;
const stripSign = (value) => String(value).replace(/^-/, "");

// Colour class for a signed amount: green above zero, red below.
export const tone = (value) => (value == null || isZero(value) ? "" : isNegative(value) ? "neg" : "pos");

// A cost shown as a deduction: "−₹1,234.00" (or "+₹…" if the cost came out negative).
export function less(value, fmt = inr) {
  if (value == null) return "—";
  if (isZero(value)) return fmt(stripSign(value));
  return isNegative(value) ? `+${fmt(stripSign(value))}` : `${MINUS}${fmt(value)}`;
}

export function signedUsd(value) {
  if (value == null) return "—";
  if (isZero(value)) return usd(stripSign(value));
  return isNegative(value) ? usd(value) : `+${usd(value)}`;
}

export function signedPct(value, digits = 2) {
  if (value == null) return "—";
  if (isZero(value)) return pct(stripSign(value), digits);
  return isNegative(value) ? pct(value, digits) : `+${pct(value, digits)}`;
}

// Show a share price with as many decimals as it has (2 to 4).
function priceText(value) {
  const fraction = (String(value).split(".")[1] || "").replace(/0+$/, "");
  return usd(value, Math.min(4, Math.max(2, fraction.length)));
}

// "262.0000" → "262.00" for an input field (keeps every significant digit).
function trimPrice(value) {
  const [whole, fraction = ""] = String(value).split(".");
  const kept = fraction.replace(/0+$/, "");
  return `${whole}.${kept.padEnd(2, "0")}`;
}

const shares = (quantity) => `${quantity} share${quantity === "1" ? "" : "s"}`;

export function marketNote(market) {
  if (!market) return "";
  if (market.source === "manual") return "rate entered by you";
  const base = [sourceLabel(market.source), intervalLabel(market.interval)].filter(Boolean).join(", ");
  return market.stale ? `${base} · last quote ${formatShort(market.bar_at)} IST` : base;
}

export function Line({ label, note, value, cls = "", total }) {
  return html`<div class=${`line ${total ? "total" : ""}`}>
    <span class=${total ? "" : "k"}>${label}${note && html`<span class="ins-note">${note}</span>`}</span>
    <span class=${`ins-val ${cls}`}>${value}</span>
  </div>`;
}

// "Where it came from": the six rupee parts that add up exactly to the net result.
export function PartsLines({ data }) {
  const p = data.parts;
  return html`
    <${Line} label="Stock price gain" note="Change in the share price, in rupees" value=${signedInr(p.stock_gain_inr)} cls=${tone(p.stock_gain_inr)} />
    <${Line} label="Currency movement" note="The rupee's move since you bought the dollars" value=${signedInr(p.currency_inr)} cls=${tone(p.currency_inr)} />
    <${Line} label="Brokerage (buy + sell)" note="Including GST on brokerage" value=${less(p.brokerage_inr)} />
    <${Line} label="Forex markup & charges on the way in" value=${less(p.entry_costs_inr)} />
    <${Line} label="Forex markup on the way out" value=${less(p.exit_markup_inr)} />
    <${Line} label="GST on conversion" value=${less(p.conversion_gst_inr)} />
    <${Line} total label="Net" value=${signedInr(data.net_inr)} cls=${tone(data.net_inr)} />
  `;
}

export function CostShare({ data }) {
  if (data.costs_share_pct == null) return null;
  return html`<div class="result-note">
    <${Icon} name="info" size=${14} />
    <span>Fees and markups took ${inr(data.costs_inr)}, ${pct(data.costs_share_pct, 1)} of your ${inr(data.gross_gain_inr)} gross gain.</span>
  </div>`;
}

// ---- page ------------------------------------------------------------------------------------

const NO_FEES = { brokerage: "", gst: "", other: "" }; // actual fees belong to one sale; cleared on a new pick
const SLABS = ["0", "5", "10", "15", "20", "30"];
const normSlab = (value) => {
  const text = String(value ?? "30");
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
};

function priceSourceOf(holding) {
  return holding?.price ? { as_of: holding.price.as_of, origin: holding.price.origin } : null;
}

function initialForm(platforms, params, settings) {
  const wanted = (params.ticker || "").trim().toUpperCase();
  const platform =
    platforms.find((p) => p.platform_id === params.platform) ||
    (wanted && platforms.find((p) => p.holdings.some((h) => h.ticker === wanted))) ||
    platforms.find((p) => p.platform_id === settings.default_platform) ||
    platforms[0];
  const holding = platform.holdings.find((h) => h.ticker === wanted) || platform.holdings[0];
  const quantity = params.quantity && /^\d*\.?\d+$/.test(params.quantity) ? params.quantity : holding.quantity;
  return {
    platform: platform.platform_id,
    ticker: holding.ticker,
    quantity,
    price: holding.price ? trimPrice(holding.price.price_usd) : "",
    priceSource: priceSourceOf(holding),
    markup: "",
    markupEdited: false,
    includeGst: settings.estimate_conversion_gst === "1",
    includeTax: true,
    slab: normSlab(settings.tax_slab_pct),
    brokerage: "",
    gst: "",
    other: "",
  };
}

function payload(form) {
  const body = {
    platform_id: form.platform,
    ticker: form.ticker,
    quantity: form.quantity,
    price_usd: form.price,
    include_gst: form.includeGst,
    include_tax: form.includeTax,
  };
  if (form.includeTax) body.slab_pct = form.slab;
  if (form.markupEdited) body.exit_markup_pct = form.markup;
  if (form.brokerage || form.gst || form.other) {
    body.brokerage_usd = form.brokerage || "0";
    body.brokerage_gst_usd = form.gst || "0";
    body.other_fees_usd = form.other || "0";
  }
  return body;
}

export function CalculatorPage({ params }) {
  const meta = useMeta();
  const [platforms, setPlatforms] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [form, setForm] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [markupSource, setMarkupSource] = useState(null);
  const requestId = useRef(0);

  useEffect(() => {
    api
      .get("/api/holdings")
      .then((d) => setPlatforms(d.platforms.filter((p) => p.holdings.length > 0)))
      .catch((e) => setLoadError(e.message));
  }, []);

  // (Re)start from the URL — e.g. the Stocks page's "Sell…" link.
  useEffect(() => {
    if (platforms && platforms.length) {
      setForm(initialForm(platforms, params, meta.settings));
      setMarkupSource(null);
    }
  }, [platforms, params.platform, params.ticker, params.quantity]);

  const ready =
    form &&
    positive(form.quantity) &&
    positive(form.price) &&
    (!form.markupEdited || /\d/.test(form.markup));
  const body = ready ? payload(form) : null;
  const key = JSON.stringify(body);

  // Every figure comes from the server; debounce so typing doesn't flood it.
  useEffect(() => {
    const id = ++requestId.current;
    if (!body) {
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .post("/api/calculator", body)
        .then((r) => {
          if (id !== requestId.current) return;
          setResult(r);
          setError(null);
          setMarkupSource(r.inputs.exit_markup_source);
          setForm((f) => (f.markupEdited ? f : { ...f, markup: r.inputs.exit_markup_pct }));
        })
        .catch((e) => id === requestId.current && setError(e.message))
        .finally(() => id === requestId.current && setLoading(false));
    }, 400);
    return () => clearTimeout(timer);
  }, [key]);

  const header = html`<header class="page-header">
    <div>
      <h1>Sell calculator</h1>
      <p>If you sell shares now and bring the money home, what you actually make in rupees.</p>
    </div>
  </header>`;

  if (loadError) {
    return html`<div class="page">${header}<div class="callout error"><${Icon} name="alert" />${loadError}</div></div>`;
  }
  if (!platforms) {
    return html`<div class="page">${header}<${CalcSkeleton} /></div>`;
  }
  if (platforms.length === 0) {
    return html`<div class="page">
      ${header}
      <div class="card">
        <${EmptyState}
          icon="calculator"
          title="No shares to sell yet"
          action=${html`<a class="btn btn-primary" href="#/stocks"><${Icon} name="layers" />Go to Stocks</a>`}
        >
          Record the shares you bought on the Stocks page. The calculator then shows what selling them
          would bring home in rupees, after every fee and markup.
        </${EmptyState}>
      </div>
    </div>`;
  }
  if (!form) return html`<div class="page">${header}<${CalcSkeleton} /></div>`;

  return html`<div class="page">
    ${header}
    <div class="ins-calc">
      <${Inputs}
        form=${form}
        setForm=${setForm}
        platforms=${platforms}
        meta=${meta}
        markupSource=${markupSource}
      />
      <div class="ins-results">
        ${error && html`<div class="callout error"><${Icon} name="alert" />${error}</div>`}
        ${!ready &&
        html`<div class="callout"><${Icon} name="info" />Enter the number of shares and a price per share to see the result.</div>`}
        ${ready && !error && !result && html`<${ResultSkeleton} />`}
        ${ready && !error && result &&
        html`<div class=${`ins-stack ${loading ? "ins-dim" : ""}`} aria-busy=${loading}>
          <${Results} r=${result} meta=${meta} loading=${loading} />
        </div>`}
      </div>
    </div>
  </div>`;
}

function Inputs({ form, setForm, platforms, meta, markupSource }) {
  const [showFees, setShowFees] = useState(false);
  const [fetching, setFetching] = useState(false);
  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));

  const platform = platforms.find((p) => p.platform_id === form.platform) || platforms[0];
  const holding = platform.holdings.find((h) => h.ticker === form.ticker);
  const name = platformName(meta, platform.platform_id);

  const choosePlatform = (id) => {
    const next = platforms.find((p) => p.platform_id === id);
    const first = next.holdings[0];
    setForm((f) => ({
      ...f,
      platform: id,
      ticker: first.ticker,
      quantity: first.quantity,
      price: first.price ? trimPrice(first.price.price_usd) : "",
      priceSource: priceSourceOf(first),
      markup: "",
      markupEdited: false,
      ...NO_FEES,
    }));
  };
  const chooseTicker = (ticker) => {
    const h = platform.holdings.find((x) => x.ticker === ticker);
    setForm((f) => ({
      ...f,
      ticker,
      quantity: h.quantity,
      price: h.price ? trimPrice(h.price.price_usd) : "",
      priceSource: priceSourceOf(h),
      ...NO_FEES,
    }));
  };

  const fetchLatest = async () => {
    setFetching(true);
    try {
      const p = await api.post(`/api/prices/${encodeURIComponent(form.ticker)}/fetch`);
      setForm((f) =>
        f.ticker === p.ticker ? { ...f, price: trimPrice(p.price_usd), priceSource: { as_of: p.as_of, origin: p.origin } } : f,
      );
      toast(`${p.ticker} price updated`);
    } catch (e) {
      toast(e.message, "error");
    }
    setFetching(false);
  };

  const priceHint = form.priceSource
    ? `${form.priceSource.origin === "auto" ? "Latest price" : "Your saved price"}, as of ${formatShort(form.priceSource.as_of)} IST`
    : form.price
      ? "Entered here"
      : "No saved price. Enter one or fetch the latest";

  const markupHint = form.markupEdited
    ? html`Entered by you · <button type="button" class="link" onClick=${() => setForm((f) => ({ ...f, markup: "", markupEdited: false }))}>Use default</button>`
    : markupSource === "history"
      ? `Your average on ${name}`
      : markupSource === "platform"
        ? html`${name} default · <a class="link" href="#/settings">edit in Settings</a>`
        : "Your average withdrawal markup, else the platform default";

  const slabOptions = (SLABS.includes(form.slab) ? SLABS : [...SLABS, form.slab]).map((s) => ({ value: s, label: `${s}%` }));

  return html`<div class="card card-pad ins-inputs">
    <div class="stack">
      <${Field} label="Platform">
        <${Select}
          value=${form.platform}
          onChange=${choosePlatform}
          options=${platforms.map((p) => ({ value: p.platform_id, label: platformName(meta, p.platform_id) }))}
        />
      </${Field}>
      <${Field} label="Stock">
        <${Select}
          value=${form.ticker}
          onChange=${chooseTicker}
          options=${platform.holdings.map((h) => ({ value: h.ticker, label: `${h.ticker} · ${shares(h.quantity)}` }))}
        />
      </${Field}>
      <${Field}
        id="calc-shares"
        label="Shares to sell"
        aside=${holding &&
        html`<button type="button" class="link field-hint" onClick=${() => set("quantity")(holding.quantity)}>All ${holding.quantity}</button>`}
      >
        <${MoneyInput} id="calc-shares" value=${form.quantity} onInput=${set("quantity")} decimals=${8} placeholder="0" />
      </${Field}>
      <${Field}
        id="calc-price"
        label="Price per share"
        hint=${priceHint}
        aside=${html`<button type="button" class="link field-hint ins-inline" onClick=${fetchLatest} disabled=${fetching}>
          ${fetching && html`<${Spinner} size=${12} />`}Fetch latest
        </button>`}
      >
        <${MoneyInput}
          id="calc-price"
          prefix="$"
          value=${form.price}
          onInput=${(v) => setForm((f) => ({ ...f, price: v, priceSource: null }))}
          decimals=${4}
        />
      </${Field}>
      <${Field} label="Exit forex markup" hint=${markupHint}>
        <${MoneyInput}
          suffix="%"
          value=${form.markup}
          onInput=${(v) => setForm((f) => ({ ...f, markup: v, markupEdited: true }))}
          decimals=${3}
          placeholder=${markupSource ? "0.000" : "…"}
        />
      </${Field}>

      <div class="ins-checks">
        <label class="ins-check">
          <input type="checkbox" checked=${form.includeGst} onChange=${(e) => set("includeGst")(e.target.checked)} />
          <span>Include GST on conversion</span>
        </label>
        <label class="ins-check">
          <input type="checkbox" checked=${form.includeTax} onChange=${(e) => set("includeTax")(e.target.checked)} />
          <span>Estimate capital gains tax</span>
        </label>
        ${form.includeTax &&
        html`<div class="ins-check-sub">
          <${Field} label="Tax slab" hint="Your income-tax slab, for short-term gains">
            <${Select} value=${form.slab} onChange=${set("slab")} options=${slabOptions} />
          </${Field}>
        </div>`}
      </div>

      <div>
        <${Disclosure} label="Actual sell fees" open=${showFees} onToggle=${() => setShowFees(!showFees)} />
        ${showFees &&
        html`<div class="stack" style="margin-top:12px;gap:12px">
          <p class="field-hint" style="margin:0">
            From your contract note, if you've already placed the sale. Leave empty to estimate them from
            ${name}'s fee rule.
          </p>
          <div class="ins-fee-row">
            <${Field} label="Brokerage">
              <${MoneyInput} prefix="$" value=${form.brokerage} onInput=${set("brokerage")} placeholder="0.00" />
            </${Field}>
            <${Field} label="GST">
              <${MoneyInput} prefix="$" value=${form.gst} onInput=${set("gst")} placeholder="0.00" />
            </${Field}>
            <${Field} label="Other">
              <${MoneyInput} prefix="$" value=${form.other} onInput=${set("other")} placeholder="0.00" />
            </${Field}>
          </div>
        </div>`}
      </div>
    </div>
  </div>`;
}

function Results({ r, meta, loading }) {
  const inputs = r.inputs;
  const showTax = r.tax && r.tax.complete && r.net_after_tax_inr != null;
  return html`
    <section class="card ins-headline" aria-live="polite">
      <div class="ins-headline-main">
        <div class="stat-label">Net profit if you sell and convert now ${loading && html`<${Spinner} size=${12} />`}</div>
        <div class=${`ins-big ${tone(r.net_inr)}`}>${signedInr(r.net_inr)}</div>
        <div class="stat-sub">
          ${r.net_pct != null ? html`${signedPct(r.net_pct)} on ${inr(r.inr_cost)} invested` : html`On ${inr(r.inr_cost)} invested`}
        </div>
      </div>
      ${showTax &&
      html`<div class="ins-headline-side">
        <div class="stat-label">After estimated tax</div>
        <div class=${`ins-mid ${tone(r.net_after_tax_inr)}`}>${signedInr(r.net_after_tax_inr)}</div>
        <div class="stat-sub">${inr(r.tax.tax_inr)} capital gains tax</div>
      </div>`}
    </section>

    <div class="ins-grid-2">
      <section class="result">
        <div class="result-head"><span class="label">How the money flows</span></div>
        <div class="result-lines ins-first">
          <${Line} label="Sale value" note=${`${shares(inputs.quantity)} of ${inputs.ticker} at ${priceText(inputs.price_usd)}`} value=${usd(r.sale_gross_usd)} />
          <${Line}
            label="Sell fees"
            note=${r.fees.estimated ? `Estimated from ${platformName(meta, inputs.platform_id)}'s fee rule` : "As entered"}
            value=${less(r.sell_fees_usd, usd)}
          />
          <${Line} total label="You receive" note="In your USD wallet" value=${usd(r.usd_out)} />
        </div>
        <div class="result-lines">
          <${Line} label=${`Converted at mid-market ${rate(r.market.rate, 4)}`} note=${marketNote(r.market)} value=${inr(r.market_inr)} />
          <${Line} label="Exit forex markup" note=${`${pct(inputs.exit_markup_pct, 3)} below mid-market`} value=${less(r.exit_markup_inr)} />
          ${inputs.include_gst &&
          html`<${Line} label="GST on conversion" note="Estimated from the government slab" value=${less(r.conversion_gst_inr)} />`}
          <${Line} total label="₹ in your bank" value=${inr(r.inr_received)} />
        </div>
        <div class="result-lines">
          <${Line} label="What these shares cost you" note="All-in ₹, including buy brokerage and markups" value=${less(r.inr_cost)} />
          <${Line} total label="Net" value=${signedInr(r.net_inr)} cls=${tone(r.net_inr)} />
        </div>
      </section>

      <section class="result">
        <div class="result-head"><span class="label">Where it came from</span></div>
        <div class="result-lines ins-first"><${PartsLines} data=${r} /></div>
        <${CostShare} data=${r} />
      </section>
    </div>

    <div class="ins-grid-2 ins-tiles">
      <${Stat}
        label="Break-even price"
        value=${r.break_even_price_usd == null ? "—" : usd(r.break_even_price_usd)}
        sub="Per share. The lowest price that returns your full rupee cost at today's rate"
      />
      <${Stat}
        label="Keep the dollars in your wallet"
        value=${html`<span class=${tone(r.keep_in_usd)}>${signedUsd(r.keep_in_usd)}</span>`}
        sub="Profit in dollars if you sell but don't convert back"
      />
    </div>

    ${r.tax && html`<${TaxCard} tax=${r.tax} />`}

    <section class="card">
      <div class="ins-card-head">
        <h3>Lots sold</h3>
        <span class="field-hint">Oldest shares first (FIFO)</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Acquired</th>
              <th class="r">Shares</th>
              <th class="r">Cost</th>
              <th class="r">₹ cost</th>
            </tr>
          </thead>
          <tbody>
            ${r.lots.map(
              (lot) => html`<tr key=${`${lot.trade_id}-${lot.acquired_at}`}>
                <td class="nowrap">${formatDate(lot.acquired_at)}</td>
                <td class="r">${lot.quantity}</td>
                <td class="r">${usd(lot.cost_usd)}</td>
                <td class="r">${inr(lot.inr_cost)}</td>
              </tr>`,
            )}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function TaxCard({ tax }) {
  return html`<section class="card">
    <div class="ins-card-head">
      <h3>Capital gains tax</h3>
      <span class="badge">Estimate</span>
    </div>
    ${!tax.complete &&
    html`<div class="callout" style="margin:12px 16px 0">
      <${Icon} name="alert" />
      <span>SBI's rates aren't available for some of these dates (its archive starts in 2020), so lots without a rate are left out of the totals below.</span>
    </div>`}
    <div class="result-lines ins-first">
      <${Line} label="Short-term gain" note="Held 24 months or less" value=${signedInr(tax.short_term_gain_inr)} cls=${tone(tax.short_term_gain_inr)} />
      <${Line} label="Long-term gain" note="Held over 24 months" value=${signedInr(tax.long_term_gain_inr)} cls=${tone(tax.long_term_gain_inr)} />
      <${Line} label="Taxable short-term, after set-off" note=${`At your ${normSlab(tax.slab_pct)}% slab`} value=${inr(tax.taxable_short_inr)} />
      <${Line} label="Taxable long-term, after set-off" note="At 12.5%" value=${inr(tax.taxable_long_inr)} />
      <${Line} total label="Estimated tax (incl. 4% cess)" value=${inr(tax.tax_inr)} />
    </div>
    <div class="table-wrap ins-table-top">
      <table>
        <thead>
          <tr>
            <th>Acquired</th>
            <th class="r">Shares</th>
            <th>Holding</th>
            <th class="r">SBI rate · bought → sold</th>
            <th class="r">Gain</th>
          </tr>
        </thead>
        <tbody>
          ${tax.lots.map(
            (lot) => html`<tr key=${`${lot.trade_id}-${lot.acquired_at}`}>
              <td class="nowrap">${formatDate(lot.acquired_at)}</td>
              <td class="r">${lot.quantity}</td>
              <td><span class=${`badge ${lot.long_term ? "solid" : ""}`}>${lot.long_term ? "Long-term" : "Short-term"}</span></td>
              <td class="r nowrap">${lot.cost_rate == null ? "—" : rate(lot.cost_rate, 4)} → ${lot.sale_rate == null ? "—" : rate(lot.sale_rate, 4)}</td>
              <td class="r nowrap">
                ${lot.gain_inr == null
                  ? html`<span class="subtle">No rate</span>`
                  : html`<span class=${tone(lot.gain_inr)}>${signedInr(lot.gain_inr)}</span>`}
              </td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>
    <div class="result-note">
      <${Icon} name="info" size=${14} />
      <span>
        Each side converted at SBI's TT buying rate on the last day of the month before that transaction
        (Rule 115; Rule 206 under the Income-tax Act, 2025). Held over 24 months is long-term at 12.5%.
        Surcharge not included. An estimate — confirm with your CA.
      </span>
    </div>
  </section>`;
}

function CalcSkeleton() {
  return html`<div class="ins-calc">
    <div class="card card-pad stack">
      ${[1, 2, 3, 4, 5].map(
        () => html`<div>
          <div class="skeleton" style="height:12px;width:35%" />
          <div class="skeleton" style="height:36px;width:100%;margin-top:8px" />
        </div>`,
      )}
    </div>
    <${ResultSkeleton} />
  </div>`;
}

function ResultSkeleton() {
  return html`<div class="ins-stack">
    <div class="card card-pad">
      <div class="skeleton" style="height:12px;width:30%" />
      <div class="skeleton" style="height:34px;width:45%;margin-top:10px" />
      <div class="skeleton" style="height:12px;width:25%;margin-top:10px" />
    </div>
    <div class="ins-grid-2">
      ${[1, 2].map(
        () => html`<div class="result" style="padding:16px">
          <div class="skeleton" style="height:12px;width:40%" />
          ${[1, 2, 3, 4, 5].map(() => html`<div class="skeleton" style="height:12px;width:100%;margin-top:12px" />`)}
        </div>`,
      )}
    </div>
  </div>`;
}
