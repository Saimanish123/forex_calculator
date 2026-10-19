import { useCallback, useEffect, useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { MoneyInput, Spinner, toast } from "../components/ui.js";
import { formatDay, formatShort, inr, intervalLabel, pct, rate, sourceLabel, todayIst } from "../format.js";
import { html } from "../html.js";

export function RatesPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [entering, setEntering] = useState(null); // bank id with the manual-rate form open

  const load = useCallback(
    () =>
      api
        .get("/api/rates/today")
        .then((d) => (setData(d), setError(null)))
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => void load(), [load]);

  return html`<div class="page">
    <header class="page-header">
      <div>
        <h1>Rates</h1>
        <p>Today's USD/INR bank rates, measured against the mid-market rate.</p>
      </div>
      <button class="btn" onClick=${() => (setData(null), load())}><${Icon} name="refresh" />Refresh</button>
    </header>

    ${error && html`<div class="callout error" style="margin-bottom:16px"><${Icon} name="alert" />${error}</div>`}

    <${MidMarket} mid=${data?.mid} loading=${!data && !error} />

    <h2 class="section-title">Banks</h2>
    <p class="section-sub">The cost of buying dollars from each bank (TT selling rate) and selling them back (TT buying rate).</p>
    <div class="card table-wrap">
      <table>
        <thead>
          <tr>
            <th>Bank</th>
            <th class="r">You buy $ at</th>
            <th class="r">You sell $ at</th>
            <th class="r">Round-trip cost</th>
            <th>Rate sheet</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${data
            ? data.banks.map(
                (bank) => html`
                  <${BankRow} bank=${bank} key=${bank.id} onEnter=${() => setEntering(bank.id)} />
                  ${entering === bank.id &&
                  html`<${ManualRateRow}
                    bank=${bank}
                    onCancel=${() => setEntering(null)}
                    onSaved=${() => (setEntering(null), load())}
                  />`}
                `,
              )
            : [1, 2, 3].map(
                () => html`<tr>
                  <td colspan="6"><div class="skeleton" style="height:16px;width:100%" /></td>
                </tr>`,
              )}
        </tbody>
      </table>
    </div>

    <h2 class="section-title">What you've actually paid</h2>
    <p class="section-sub">Average markup per platform, from the conversions you've recorded.</p>
    <${PlatformHistory} platforms=${data?.platforms} />

    <p class="field-hint" style="margin-top:20px">
      Markups are measured against the mid-market rate. TT (telegraphic transfer) rates are what banks use for
      transfers. Published card rates apply to smaller amounts; large transfers may get a negotiated rate.
    </p>
  </div>`;
}

function MidMarket({ mid, loading }) {
  if (loading) {
    return html`<div class="card card-pad"><div class="skeleton" style="height:56px;width:40%" /></div>`;
  }
  if (!mid) {
    return html`<div class="callout error"><${Icon} name="alert" />Couldn't fetch the mid-market rate right now.</div>`;
  }
  return html`<div class="card card-pad" style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap">
    <div>
      <div class="stat-label">Mid-market · USD/INR</div>
      <div class="stat-value" style="font-size:34px">${rate(mid.rate, 4)}</div>
    </div>
    <div style="text-align:right" class="field-hint">
      ${mid.stale && html`<span class="badge solid" style="margin-bottom:6px"><span class="dot" />No live quote</span><br />`}
      ${sourceLabel(mid.source)} · ${intervalLabel(mid.interval)} · last quote ${formatShort(mid.bar_at)} IST
    </div>
  </div>`;
}

function Markup({ value }) {
  return value == null ? null : html`<div class="cell-sub">${pct(value)} vs mid</div>`;
}

function BankRow({ bank, onEnter }) {
  const r = bank.rate;
  const failed = bank.auto && bank.status && !bank.status.ok;
  const stale = r && daysBetween(r.date, todayIst()) > 3;
  return html`<tr>
    <td>
      <div style="font-weight:500">${bank.name}</div>
      <div class="cell-sub">${bank.auto ? "Fetched automatically" : "Entered manually"}</div>
    </td>
    <td class="r">${r ? html`${rate(r.tt_sell)}<${Markup} value=${r.buy_usd_markup_pct} />` : html`<span class="subtle">—</span>`}</td>
    <td class="r">${r ? html`${rate(r.tt_buy)}<${Markup} value=${r.sell_usd_markup_pct} />` : html`<span class="subtle">—</span>`}</td>
    <td class="r">${r ? pct(r.round_trip_pct) : html`<span class="subtle">—</span>`}</td>
    <td>
      ${r
        ? html`${formatDay(r.date)}<div class="cell-sub">${stale ? "Out of date" : r.origin === "manual" ? "Entered by you" : `Published ${formatShort(r.published_at)}`}</div>`
        : failed
          ? html`<span class="neg">Couldn't fetch</span><div class="cell-sub">Enter today's rate instead</div>`
          : html`<span class="subtle">No rate yet</span>`}
    </td>
    <td class="r">
      ${(!bank.auto || failed || stale) &&
      html`<button class="btn btn-sm" onClick=${onEnter}><${Icon} name="plus" size=${14} />Add rate</button>`}
    </td>
  </tr>`;
}

function ManualRateRow({ bank, onCancel, onSaved }) {
  const [day, setDay] = useState(todayIst());
  const [ttSell, setTtSell] = useState("");
  const [ttBuy, setTtBuy] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const save = async () => {
    setSaving(true);
    try {
      await api.post("/api/bank-rates", { bank_id: bank.id, rate_date: day, tt_buy: ttBuy, tt_sell: ttSell });
      toast(`${bank.name} rate saved`);
      onSaved();
    } catch (e) {
      setError(e.message);
      setSaving(false);
    }
  };

  return html`<tr>
    <td colspan="6" style="background:var(--bg-subtle)">
      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap">
        <label class="field" style="width:170px">
          <span class="field-label">Date</span>
          <input class="input" type="date" value=${day} max=${todayIst()} onInput=${(e) => setDay(e.target.value)} />
        </label>
        <label class="field" style="width:170px">
          <span class="field-label">TT selling (you buy $)</span>
          <${MoneyInput} prefix="₹" value=${ttSell} onInput=${setTtSell} decimals=${4} autofocus />
        </label>
        <label class="field" style="width:170px">
          <span class="field-label">TT buying (you sell $)</span>
          <${MoneyInput} prefix="₹" value=${ttBuy} onInput=${setTtBuy} decimals=${4} />
        </label>
        <div style="display:flex;gap:8px">
          <button class="btn" onClick=${onCancel}>Cancel</button>
          <button class="btn btn-primary" onClick=${save} disabled=${saving || !Number(ttSell) || !Number(ttBuy)}>
            ${saving && html`<${Spinner} />`}Save rate
          </button>
        </div>
      </div>
      ${error && html`<div class="field-error" style="margin-top:8px">${error}</div>`}
    </td>
  </tr>`;
}

function PlatformHistory({ platforms }) {
  if (!platforms) return html`<div class="card card-pad"><div class="skeleton" style="height:16px;width:60%" /></div>`;
  if (platforms.length === 0) {
    return html`<div class="card card-pad muted">Record a few conversions to see what each platform really charges you.</div>`;
  }
  return html`<div class="card table-wrap">
    <table>
      <thead>
        <tr>
          <th>Platform</th>
          <th class="r">Conversions</th>
          <th class="r">Avg markup · INR → USD</th>
          <th class="r">Avg markup · USD → INR</th>
          <th class="r">Hidden cost</th>
        </tr>
      </thead>
      <tbody>
        ${platforms.map(
          (p) => html`<tr key=${p.id}>
            <td style="font-weight:500">${p.name}</td>
            <td class="r">${p.deposits.count + p.withdrawals.count}</td>
            <td class="r">${p.deposits.count ? pct(p.deposits.avg_markup_pct) : html`<span class="subtle">—</span>`}</td>
            <td class="r">${p.withdrawals.count ? pct(p.withdrawals.avg_markup_pct) : html`<span class="subtle">—</span>`}</td>
            <td class="r">${inr(p.hidden_cost_inr)}</td>
          </tr>`,
        )}
      </tbody>
    </table>
  </div>`;
}

function daysBetween(fromIso, toIso) {
  return Math.round((Date.parse(`${toIso}T00:00:00Z`) - Date.parse(`${fromIso}T00:00:00Z`)) / 86400000);
}
