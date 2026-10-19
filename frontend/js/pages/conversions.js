import { useCallback, useEffect, useMemo, useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { EmptyState, Segmented, Select, Spinner, Stat, toast } from "../components/ui.js";
import { formatDate, formatTime, inr, isNegative, isZero, pct, rate, signedInr, usd } from "../format.js";
import { html } from "../html.js";
import { bankName, platformName, useMeta } from "../meta.js";
import { ConversionDrawer } from "./conversion-drawer.js";

const isTyping = (event) => ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);

export function ConversionsPage() {
  const meta = useMeta();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [direction, setDirection] = useState("all");
  const [platform, setPlatform] = useState("all");
  const [editing, setEditing] = useState(null); // null | "new" | entry
  const [loadingSample, setLoadingSample] = useState(false);

  const load = useCallback(
    () =>
      api
        .get("/api/forex-entries")
        .then((d) => (setData(d), setError(null)))
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => void load(), [load]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key.toLowerCase() === "n" && !editing && !isTyping(event) && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        setEditing("new");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);

  const entries = data?.entries ?? [];
  const usedPlatforms = useMemo(() => [...new Set(entries.map((e) => e.platform_id))], [entries]);
  const visible = entries.filter(
    (e) => (direction === "all" || e.kind === direction) && (platform === "all" || e.platform_id === platform),
  );
  const close = useCallback(() => setEditing(null), []);
  const dividendView = direction === "dividend";

  const loadSample = async () => {
    setLoadingSample(true);
    try {
      await api.post("/api/sample-data");
      await Promise.all([meta.reload(), load()]);
      toast("Sample data loaded");
    } catch (e) {
      toast(e.message, "error");
    }
    setLoadingSample(false);
  };

  return html`<div class="page">
    <header class="page-header">
      <div>
        <h1>Conversions</h1>
        <p>Every INR ↔ USD transfer, measured against the market rate at that minute, and every dividend received.</p>
      </div>
      <button class="btn btn-primary" onClick=${() => setEditing("new")}>
        <${Icon} name="plus" />${dividendView ? "New dividend" : "New conversion"}<span class="kbd">N</span>
      </button>
    </header>

    ${error && html`<div class="callout error" style="margin-bottom:16px"><${Icon} name="alert" />${error}</div>`}

    <${Summary} summary=${data?.summary} loading=${!data && !error} />

    ${data &&
    (entries.length === 0
      ? html`<div class="card">
          <${EmptyState}
            icon="arrows"
            title="No conversions yet"
            action=${html`<div class="empty-actions">
              <button class="btn btn-primary" onClick=${() => setEditing("new")}>
                <${Icon} name="plus" />Add your first conversion
              </button>
              ${meta.empty &&
              html`<button class="btn" onClick=${loadSample} disabled=${loadingSample}>
                ${loadingSample ? html`<${Spinner} />` : html`<${Icon} name="download" />`}Load sample data
              </button>`}
            </div>`}
          >
            Add a deposit, withdrawal or dividend from your ${platformName(meta, meta.settings.default_platform)} history.
            The app looks up the market rate at that minute and shows what the conversion really cost you.
          </${EmptyState}>
        </div>`
      : html`
          <div class="toolbar">
            <${Segmented}
              value=${direction}
              onChange=${setDirection}
              options=${[
                { value: "all", label: "All" },
                { value: "deposit", label: "INR → USD" },
                { value: "withdrawal", label: "USD → INR" },
                { value: "dividend", label: "Dividends" },
              ]}
            />
            ${usedPlatforms.length > 1 &&
            html`<div style="width:200px">
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
            <${EntriesTable} entries=${visible} meta=${meta} onOpen=${setEditing} />
            ${visible.length === 0 &&
            html`<div class="empty" style="padding:32px"><p style="margin:0">${dividendView ? "No dividends match these filters." : "No entries match these filters."}</p></div>`}
          </div>
        `)}

    ${editing &&
    html`<${ConversionDrawer}
      entry=${editing === "new" ? null : editing}
      initialKind=${dividendView ? "dividend" : undefined}
      onClose=${close}
      onSaved=${() => (close(), load())}
    />`}
  </div>`;
}

function Summary({ summary, loading }) {
  const s = summary;
  const count = s ? s.deposits.count + s.withdrawals.count : 0;
  const vs = s?.vs_bank_inr;
  return html`<div class="stats">
    <${Stat}
      loading=${loading}
      label="Hidden cost"
      value=${s ? inr(s.hidden_cost_inr, 0) : ""}
      sub=${s && (count ? `Markups + charges on ${count} conversion${count === 1 ? "" : "s"}` : "Nothing recorded yet")}
    />
    <${Stat}
      loading=${loading}
      label="Avg markup · INR → USD"
      value=${s?.deposits.count ? pct(s.deposits.avg_markup_pct) : "—"}
      sub=${s && (s.deposits.count ? `On ${inr(s.deposits.inr, 0)} sent` : "No deposits yet")}
    />
    <${Stat}
      loading=${loading}
      label="Avg markup · USD → INR"
      value=${s?.withdrawals.count ? pct(s.withdrawals.avg_markup_pct) : "—"}
      sub=${s && (s.withdrawals.count ? `On ${usd(s.withdrawals.usd, 0)} withdrawn` : "No withdrawals yet")}
    />
    <${Stat}
      loading=${loading}
      label="Compared with bank rates"
      value=${vs == null
        ? "—"
        : html`<span class=${isZero(vs) ? "" : isNegative(vs) ? "neg" : "pos"}>${signedInr(vs, 0)}</span>`}
      sub=${s &&
      (vs == null
        ? "No bank rates to compare yet"
        : `${isNegative(vs) ? "More than" : "Saved vs"} banks on ${s.vs_bank_count} conversion${s.vs_bank_count === 1 ? "" : "s"}`)}
    />
  </div>`;
}

function EntriesTable({ entries, meta, onOpen }) {
  return html`<table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Direction</th>
        <th>Platform</th>
        <th class="r">Amount</th>
        <th class="r">Your rate</th>
        <th class="r">Market rate</th>
        <th class="r">Hidden cost</th>
        <th class="r">vs bank</th>
      </tr>
    </thead>
    <tbody>
      ${entries.map((e) => {
        if (e.kind === "dividend") return html`<${DividendRow} key=${e.id} entry=${e} meta=${meta} onOpen=${onOpen} />`;
        const a = e.analysis;
        const deposit = e.kind === "deposit";
        return html`<tr
          class="clickable"
          key=${e.id}
          tabindex="0"
          onClick=${() => onOpen(e)}
          onKeyDown=${(ev) => ev.key === "Enter" && onOpen(e)}
        >
          <td class="nowrap">${formatDate(e.occurred_at)}<div class="cell-sub">${formatTime(e.occurred_at)} IST</div></td>
          <td><span class="badge">${deposit ? "INR → USD" : "USD → INR"}</span></td>
          <td>${platformName(meta, e.platform_id)}</td>
          <td class="r nowrap">
            ${deposit ? inr(e.inr_amount) : usd(e.usd_amount)}
            <div class="cell-sub">→ ${deposit ? usd(e.usd_amount) : inr(e.inr_amount)}</div>
          </td>
          <td class="r">${rate(a.rate_got)}</td>
          <td class="r">
            ${rate(e.market.rate)}
            <div class="cell-sub">${marketTag(e.market)}</div>
          </td>
          <td class="r nowrap">
            ${inr(a.hidden_cost_inr)}
            <div class="cell-sub">${pct(a.markup_pct)} markup</div>
          </td>
          <td class="r nowrap">
            ${a.vs_bank_inr == null
              ? html`<span class="subtle">—</span>`
              : html`<span class=${isZero(a.vs_bank_inr) ? "" : isNegative(a.vs_bank_inr) ? "neg" : "pos"}>
                  ${signedInr(a.vs_bank_inr)}
                </span>`}
            <div class="cell-sub">${e.bank_id && a.vs_bank_inr != null ? bankName(meta, e.bank_id) : ""}</div>
          </td>
        </tr>`;
      })}
    </tbody>
  </table>`;
}

function DividendRow({ entry: e, meta, onOpen }) {
  const none = html`<span class="subtle">—</span>`;
  return html`<tr class="clickable" tabindex="0" onClick=${() => onOpen(e)} onKeyDown=${(ev) => ev.key === "Enter" && onOpen(e)}>
    <td class="nowrap">${formatDate(e.occurred_at)}<div class="cell-sub">${formatTime(e.occurred_at)} IST</div></td>
    <td><span class="badge">Dividend</span></td>
    <td>${platformName(meta, e.platform_id)}</td>
    <td class="r nowrap">
      ${usd(e.usd_amount)}
      <div class="cell-sub">${e.ticker} · ${usd(e.gross_usd)} before ${usd(e.withheld_usd)} US tax</div>
      <div class="cell-sub">${inr(e.income_inr)} income</div>
    </td>
    <td class="r">${none}</td>
    <td class="r">
      ${rate(e.market.rate)}
      <div class="cell-sub">${marketTag(e.market)}</div>
    </td>
    <td class="r">${none}</td>
    <td class="r">${none}</td>
  </tr>`;
}

function marketTag(market) {
  if (market.source === "manual") return "entered";
  if (market.stale) return `last quote ${formatTime(market.bar_at)}`;
  return { "1m": "1-min", "5m": "5-min", "1h": "hourly", "1d": "daily" }[market.interval] ?? "";
}
