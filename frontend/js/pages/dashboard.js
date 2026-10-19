import { useCallback, useEffect, useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { EmptyState, Spinner, Stat, toast } from "../components/ui.js";
import { formatDate, formatShort, inr, intervalLabel, isNegative, isZero, pct, rate, signedInr, sourceLabel, usd } from "../format.js";
import { html } from "../html.js";
import { platformName, useMeta } from "../meta.js";
import { CostShare, Line, PartsLines, less, marketNote, signedPct, tone } from "./calculator.js";

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
const stripSign = (value) => String(value).replace(/^-/, "");

export function DashboardPage() {
  const meta = useMeta();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(
    () =>
      api
        .get("/api/dashboard")
        .then((d) => (setData(d), setError(null)))
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => {
    if (!meta.empty) load();
  }, [meta.empty, load]);

  if (meta.empty) return html`<${Welcome} meta=${meta} />`;

  const loading = !data && !error;
  return html`<div class="page">
    <header class="page-header">
      <div>
        <h1>Dashboard</h1>
        <p>Where your money stands in rupees, after every markup, fee and currency move.</p>
      </div>
      ${data && html`<${MarketChip} market=${data.market} />`}
    </header>

    ${error && html`<div class="callout error" style="margin-bottom:16px"><${Icon} name="alert" />${error}</div>`}

    ${data?.uncovered?.length > 0 && html`<${Uncovered} items=${data.uncovered} meta=${meta} />`}

    ${!error && html`<${Stats} data=${data} loading=${loading} />`}

    ${loading && html`<${DashSkeleton} />`}

    ${data &&
    html`
      <h2 class="section-title">If you sold everything today</h2>
      <p class="section-sub">Every share sold at its latest price and every dollar converted back at today's mid-market rate, less the usual markup.</p>
      ${data.positions.length === 0
        ? html`<div class="card card-pad muted">You don't hold any shares or dollars right now.</div>`
        : html`<div class="ins-stack">
            ${data.positions.map((p) => html`<${Position} key=${p.platform_id} p=${p} market=${data.market} meta=${meta} />`)}
          </div>`}

      <div class="ins-grid-2 ins-section">
        <${Realized} r=${data.realized} />
        <${Costs} c=${data.costs} />
      </div>

      <h2 class="section-title">TCS under LRS</h2>
      <p class="section-sub">What you've sent abroad each financial year (April to March), across all platforms.</p>
      <${Tcs} years=${data.tcs} />
    `}
  </div>`;
}

function Welcome({ meta }) {
  const [busy, setBusy] = useState(false);
  const loadSample = async () => {
    setBusy(true);
    try {
      await api.post("/api/sample-data");
      await meta.reload();
      toast("Sample data loaded");
    } catch (e) {
      toast(e.message, "error");
      setBusy(false);
    }
  };
  return html`<div class="page">
    <header class="page-header">
      <div>
        <h1>Dashboard</h1>
        <p>Where your money stands in rupees.</p>
      </div>
    </header>
    <div class="card">
      <${EmptyState}
        icon="home"
        title="Track what US investing really costs you"
        action=${html`<div class="ins-actions">
          <a class="btn btn-primary" href="#/conversions"><${Icon} name="plus" />Add a conversion</a>
          <button class="btn" onClick=${loadSample} disabled=${busy}>
            ${busy ? html`<${Spinner} />` : html`<${Icon} name="download" />`}Load sample data
          </button>
        </div>`}
      >
        Record your INR ↔ USD conversions and stock trades, and Forex Ledger measures each one against the
        market rate. You see the hidden forex markup, GST and brokerage, and what you'd really take home in rupees.
      </${EmptyState}>
    </div>
  </div>`;
}

function MarketChip({ market }) {
  if (!market) {
    return html`<div class="ins-chip"><span class="muted">USD/INR market rate unavailable</span></div>`;
  }
  return html`<div class="ins-chip" title=${marketNote(market)}>
    <div class="ins-chip-label">USD/INR mid-market</div>
    <div class="ins-chip-value">${rate(market.rate, 4)}</div>
    <div class="ins-chip-sub">
      ${market.stale ? `Last quote ${formatShort(market.bar_at)} IST` : `${sourceLabel(market.source)}${market.interval ? `, ${intervalLabel(market.interval)}` : ""}`}
    </div>
  </div>`;
}

function Stats({ data, loading }) {
  const d = data;
  const withExit = d ? d.positions.filter((p) => p.exit) : [];
  const single = d && d.positions.length === 1 && withExit.length === 1 ? withExit[0] : null;
  let soldValue = "";
  let soldSub = "";
  if (d) {
    if (single) {
      soldValue = html`<span class=${tone(single.exit.net_inr)}>${signedInr(single.exit.net_inr, 0)}</span>`;
      soldSub = single.exit.net_pct != null ? `${signedPct(single.exit.net_pct)} on ${inr(single.exit.inr_cost, 0)} · estimate` : "Estimate";
    } else if (d.positions.length === 0) {
      soldValue = "—";
      soldSub = "No shares or dollars held";
    } else {
      soldValue = html`<span class="muted">See below</span>`;
      soldSub = d.positions.length > 1 ? "Shown per platform" : "Needs current prices";
    }
  }
  return html`<div class="stats">
    <${Stat}
      loading=${loading}
      label="Net invested"
      value=${d ? inr(d.net_invested_inr, 0) : ""}
      sub=${d && `${inr(d.invested_inr, 0)} in · ${inr(d.withdrawn_inr, 0)} out`}
    />
    <${Stat}
      loading=${loading}
      label="Realized so far"
      value=${d ? html`<span class=${tone(d.realized.total_inr)}>${signedInr(d.realized.total_inr, 0)}</span>` : ""}
      sub="Already back in your bank or locked in"
    />
    <${Stat}
      loading=${loading}
      label="Hidden costs paid"
      value=${d ? inr(d.costs.total_inr, 0) : ""}
      sub="Markups, GST & brokerage"
    />
    <${Stat} loading=${loading} label="If you sold everything today" value=${soldValue} sub=${soldSub} />
  </div>`;
}

function Position({ p, market, meta }) {
  const name = p.name || platformName(meta, p.platform_id);
  const head = html`<div class="ins-card-head">
    <h3>${name}</h3>
    <span class="ins-tickers">
      ${p.tickers.map((t) => html`<span class="badge" key=${t}>${t}</span>`)}
    </span>
  </div>`;

  if (p.missing_prices.length > 0) {
    return html`<section class="card">
      ${head}
      <div class="card-pad">
        <div class="callout">
          <${Icon} name="info" />
          <span>
            Add current prices for ${joinList(p.missing_prices)} on the${" "}
            <a class="link" href="#/stocks">Stocks page</a> to see this.
          </span>
        </div>
      </div>
    </section>`;
  }
  if (!p.exit) {
    return html`<section class="card">
      ${head}
      <div class="card-pad">
        <div class="callout"><${Icon} name="alert" />Couldn't get today's USD/INR rate, so this can't be estimated right now.</div>
      </div>
    </section>`;
  }

  const e = p.exit;
  const withGst = !isZero(e.conversion_gst_inr) || meta.settings.estimate_conversion_gst === "1";
  const markupNote =
    p.exit_markup_source === "history" ? `your average on ${name}` : `${name}'s default, editable in Settings`;
  return html`<section class="card">
    ${head}
    <div class="ins-position-top">
      <div>
        <div class="stat-label">Net result in rupees</div>
        <div class=${`ins-big ${tone(e.net_inr)}`}>${signedInr(e.net_inr)}</div>
        <div class="stat-sub">
          ${e.net_pct != null ? html`${signedPct(e.net_pct)} on ${inr(e.inr_cost)} invested` : html`On ${inr(e.inr_cost)} invested`}
        </div>
      </div>
      <${BreakEven} p=${p} />
    </div>
    <div class="ins-grid-2 ins-position-body">
      <div class="result">
        <div class="result-head"><span class="label">How the money flows</span></div>
        <div class="result-lines ins-first">
          <${Line} label="Shares at latest prices" value=${usd(e.sale_gross_usd)} />
          <${Line} label="Sell fees" note="Estimated from the platform's fee rule" value=${less(e.sell_fees_usd, usd)} />
          <${Line} label="Cash in the wallet" value=${isZero(e.cash_usd) ? usd(e.cash_usd) : `+${usd(e.cash_usd)}`} />
          <${Line} total label="Dollars to convert" value=${usd(e.usd_out)} />
        </div>
        <div class="result-lines">
          <${Line}
            label=${market ? `Converted at mid-market ${rate(market.rate, 4)}` : "Converted at mid-market"}
            note=${marketNote(market)}
            value=${inr(e.market_inr)}
          />
          <${Line} label="Exit forex markup" note=${`${pct(p.exit_markup_pct, 3)}, ${markupNote}`} value=${less(e.exit_markup_inr)} />
          ${withGst && html`<${Line} label="GST on conversion" note="Estimated from the government slab" value=${less(e.conversion_gst_inr)} />`}
          <${Line} total label="₹ in your bank" value=${inr(e.inr_received)} />
        </div>
        <div class="result-lines">
          <${Line} label="What it all cost you" note="All-in ₹ of these shares and dollars" value=${less(e.inr_cost)} />
          <${Line} total label="Net" value=${signedInr(e.net_inr)} cls=${tone(e.net_inr)} />
        </div>
      </div>
      <div class="result">
        <div class="result-head"><span class="label">Where it came from</span></div>
        <div class="result-lines ins-first"><${PartsLines} data=${e} /></div>
        <${CostShare} data=${e} />
      </div>
    </div>
  </section>`;
}

function BreakEven({ p }) {
  if (p.break_even_usd == null || p.gap_usd == null) return null;
  const above = isNegative(p.gap_usd) || isZero(p.gap_usd);
  return html`<div class="ins-breakeven">
    <div class="stat-label">Break-even</div>
    <div class="ins-breakeven-text">
      ${above
        ? html`You're <strong class="pos">${usd(stripSign(p.gap_usd))}</strong> above break-even`
        : html`Your portfolio needs to reach <strong>${usd(p.break_even_usd)}</strong> (+${usd(p.gap_usd)}) to get back every rupee`}
    </div>
    ${above && html`<div class="stat-sub">Break-even value ${usd(p.break_even_usd)}</div>`}
  </div>`;
}

function Realized({ r }) {
  const counts = [plural(r.sales, "sale"), plural(r.withdrawals, "withdrawal"), plural(r.dividends, "dividend")].join(" · ");
  return html`<section class="result">
    <div class="result-head"><span class="label">Realized so far</span><span class="field-hint">${counts}</span></div>
    <p class="ins-explain">Money already back in your bank, or locked in by a sale.</p>
    <div class="result-lines">
      <${Line} label="Stock gains" note="From shares you've sold" value=${signedInr(r.stock_gain_inr)} cls=${tone(r.stock_gain_inr)} />
      <${Line} label="Dividends" note="After US tax withheld" value=${signedInr(r.dividend_income_inr)} cls=${tone(r.dividend_income_inr)} />
      <${Line} label="Currency movement" note="On dollars you've withdrawn" value=${signedInr(r.currency_inr)} cls=${tone(r.currency_inr)} />
      <${Line} label="Forex costs on the way in" value=${less(r.entry_costs_inr)} />
      <${Line} label="Forex costs on the way out" value=${less(r.exit_costs_inr)} />
      <${Line} total label="Realized" value=${signedInr(r.total_inr)} cls=${tone(r.total_inr)} />
    </div>
  </section>`;
}

function Costs({ c }) {
  return html`<section class="result">
    <div class="result-head"><span class="label">Hidden costs paid</span></div>
    <p class="ins-explain">What conversions and trades have cost you beyond the market rate.</p>
    <div class="result-lines">
      <${Line} label="Forex markup · INR → USD" value=${inr(c.forex_markup_in_inr)} />
      <${Line} label="Forex markup · USD → INR" value=${inr(c.forex_markup_out_inr)} />
      <${Line} label="GST & conversion charges" value=${inr(c.conversion_charges_inr)} />
      <${Line} label="Brokerage" note="Including GST on brokerage" value=${inr(c.brokerage_inr)} />
      <${Line} total label="Total" value=${inr(c.total_inr)} />
    </div>
    ${!isZero(c.us_tax_withheld_inr) &&
    html`<div class="result-note">
      <${Icon} name="info" size=${14} />
      <span>US tax withheld on dividends: ${inr(c.us_tax_withheld_inr)}. Claimable as a foreign tax credit (Form 67).</span>
    </div>`}
  </section>`;
}

function Tcs({ years }) {
  const current = years.find((y) => y.current);
  const earlier = years.filter((y) => !y.current);
  // Bar width is presentation only; the figures shown are the server's strings.
  const fill = current ? Math.min(100, (Number(current.remitted_inr) / Number(current.threshold_inr)) * 100) : 0;
  const over = current && !isZero(current.over_threshold_inr);
  return html`<div class="ins-grid-2 ins-tcs">
    ${current &&
    html`<section class="card card-pad">
      <div class="ins-tcs-head">
        <div>
          <div class="stat-label">FY ${current.fy} · this year</div>
          <div class="ins-mid">${inr(current.remitted_inr, 0)}<small> of ${inr(current.threshold_inr, 0)}</small></div>
        </div>
        <span class="badge">${plural(current.deposits, "deposit")}</span>
      </div>
      <div class="ins-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${Math.round(fill)} aria-label="Share of the ₹10 lakh TCS threshold used">
        <div class="ins-progress-fill" style=${`width:${fill}%`} />
      </div>
      <div class="result-lines ins-flush">
        <${Line} label="Remitted this year" value=${inr(current.remitted_inr)} />
        ${over
          ? html`<${Line} label="Above the threshold" value=${inr(current.over_threshold_inr)} />`
          : html`<${Line} label="Remaining before TCS applies" value=${inr(current.remaining_inr)} />`}
        <${Line} label="TCS paid" note="Claimable in your ITR, so not a cost" value=${inr(current.tcs_paid_inr)} />
      </div>
      <p class="field-hint" style="margin:12px 0 0">
        TCS is collected at 20% above ₹10 lakh per financial year across all your LRS remittances (2% for
        education/medical).
      </p>
    </section>`}
    <section class="card">
      <div class="ins-card-head"><h3>Earlier years</h3></div>
      ${earlier.length === 0
        ? html`<div class="card-pad muted">No remittances in earlier financial years.</div>`
        : html`<div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Financial year</th>
                  <th class="r">Deposits</th>
                  <th class="r">Remitted</th>
                  <th class="r">TCS paid</th>
                </tr>
              </thead>
              <tbody>
                ${earlier.map(
                  (y) => html`<tr key=${y.fy}>
                    <td>FY ${y.fy}</td>
                    <td class="r">${y.deposits}</td>
                    <td class="r">${inr(y.remitted_inr)}</td>
                    <td class="r">${inr(y.tcs_paid_inr)}</td>
                  </tr>`,
                )}
              </tbody>
            </table>
          </div>`}
    </section>
  </div>`;
}

const UNCOVERED_KIND = {
  buy: "Stock buy",
  withdrawal: "Withdrawal",
  sell: "Stock sell",
  deposit: "Deposit",
  dividend: "Dividend",
};

function Uncovered({ items, meta }) {
  return html`<div class="callout ins-uncovered">
    <${Icon} name="alert" />
    <div>
      <div>
        These used more dollars than your recorded deposits, so they were valued at the market rate with no
        markup. Add the missing deposits for accurate figures.
      </div>
      <ul>
        ${items.map(
          (u) => html`<li key=${`${u.kind}-${u.id}`}>
            ${`${formatDate(u.occurred_at)} · ${UNCOVERED_KIND[u.kind] || u.kind} on ${platformName(meta, u.platform_id)} · ${usd(u.usd)} not covered`}
          </li>`,
        )}
      </ul>
    </div>
  </div>`;
}

function joinList(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function DashSkeleton() {
  return html`<div class="ins-stack">
    <div class="skeleton" style="height:16px;width:200px;margin-top:8px" />
    <div class="card card-pad">
      <div class="skeleton" style="height:12px;width:25%" />
      <div class="skeleton" style="height:30px;width:35%;margin-top:10px" />
      <div class="ins-grid-2" style="margin-top:20px">
        ${[1, 2].map(
          () => html`<div>
            ${[1, 2, 3, 4, 5].map(() => html`<div class="skeleton" style="height:12px;width:100%;margin-top:12px" />`)}
          </div>`,
        )}
      </div>
    </div>
  </div>`;
}
