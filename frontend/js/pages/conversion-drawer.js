import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { Disclosure, Drawer, Field, MoneyInput, Segmented, Select, Spinner, toast } from "../components/ui.js";
import {
  formatShort,
  fromIstInput,
  inr,
  intervalLabel,
  isNegative,
  isZero,
  nowIstInput,
  pct,
  rate,
  sourceLabel,
  toIstInput,
  usd,
} from "../format.js";
import { html } from "../html.js";
import { bankName, useMeta } from "../meta.js";

const positive = (value) => value !== "" && Number(value) > 0;

// US withholding on dividends for Indian residents (India–US treaty): 25% of the gross dividend.
// Integer-cent arithmetic on the typed string (no floats): cents × 25 / 100, rounded half-up to a cent.
export function quarterOf(value) {
  const match = /^(\d*)(?:\.(\d{0,2}))?$/.exec(String(value ?? "").trim());
  if (!match || (!match[1] && !match[2])) return "";
  const cents = BigInt(match[1] || "0") * 100n + BigInt((match[2] || "").padEnd(2, "0"));
  const quarter = (cents * 25n + 50n) / 100n;
  return `${quarter / 100n}.${String(quarter % 100n).padStart(2, "0")}`;
}

function initialForm(entry, settings, initialKind) {
  const form = {
    kind: initialKind || "deposit",
    platform_id: settings.default_platform,
    occurred: nowIstInput(),
    inr: "",
    usd: "",
    charges: "",
    tcs: "",
    bank_id: settings.default_bank || "",
    bank_rate: "",
    market_rate: "",
    note: "",
    ticker: "",
    gross: "",
    withheld: "",
    withheldEdited: false, // false: withheld follows 25% of the gross dividend
  };
  if (!entry) return form;
  const blankZero = (v) => (isZero(v) ? "" : v);
  Object.assign(form, {
    kind: entry.kind,
    platform_id: entry.platform_id,
    occurred: toIstInput(entry.occurred_at),
    market_rate: entry.market.source === "manual" ? entry.market.rate : "",
    note: entry.note || "",
  });
  if (entry.kind === "dividend") {
    return Object.assign(form, {
      ticker: entry.ticker,
      gross: entry.gross_usd,
      withheld: entry.withheld_usd,
      withheldEdited: entry.withheld_usd !== quarterOf(entry.gross_usd),
    });
  }
  return Object.assign(form, {
    inr: entry.inr_amount,
    usd: entry.usd_amount,
    charges: blankZero(entry.charges_inr),
    tcs: blankZero(entry.tcs_inr),
    bank_id: entry.bank_id || "",
    bank_rate: entry.bank.manual ? entry.bank.rate : "",
  });
}

// Only the fields of the selected kind are sent, so switching kinds never sends stale amounts.
function payload(form) {
  const common = {
    kind: form.kind,
    occurred_at: fromIstInput(form.occurred),
    platform_id: form.platform_id,
  };
  const tail = {
    market_rate_override: positive(form.market_rate) ? form.market_rate : null,
    note: form.note || null,
  };
  if (form.kind === "dividend") {
    return {
      ...common,
      ticker: form.ticker.trim().toUpperCase(),
      gross_usd: form.gross,
      withheld_usd: form.withheld || "0",
      ...tail,
    };
  }
  return {
    ...common,
    bank_id: form.bank_id || null,
    inr_amount: form.inr,
    usd_amount: form.usd,
    charges_inr: form.charges || "0",
    tcs_inr: form.kind === "deposit" ? form.tcs || "0" : "0",
    market_rate_override: tail.market_rate_override,
    bank_rate_override: form.bank_id && positive(form.bank_rate) ? form.bank_rate : null,
    note: tail.note,
  };
}

const NOUN = { deposit: "conversion", withdrawal: "conversion", dividend: "dividend" };

export function ConversionDrawer({ entry, initialKind, onClose, onSaved }) {
  const meta = useMeta();
  const [form, setForm] = useState(() => initialForm(entry, meta.settings, initialKind));
  const [preview, setPreview] = useState(entry);
  const [previewError, setPreviewError] = useState(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [showCharges, setShowCharges] = useState(Boolean(form.charges || form.tcs));
  const [showAdvanced, setShowAdvanced] = useState(Boolean(form.market_rate));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const requestId = useRef(0);

  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));
  const setGross = (value) =>
    setForm((f) => ({ ...f, gross: value, withheld: f.withheldEdited ? f.withheld : quarterOf(value) }));
  const setWithheld = (value) => setForm((f) => ({ ...f, withheld: value, withheldEdited: true }));
  const resetWithheld = () => setForm((f) => ({ ...f, withheld: quarterOf(f.gross), withheldEdited: false }));
  const deposit = form.kind === "deposit";
  const dividend = form.kind === "dividend";
  const noun = NOUN[form.kind];
  const Noun = noun[0].toUpperCase() + noun.slice(1);
  const bank = meta.banks.find((b) => b.id === form.bank_id);
  const ready = Boolean(
    form.occurred &&
      form.platform_id &&
      (dividend ? form.ticker.trim() && positive(form.gross) : positive(form.inr) && positive(form.usd)),
  );
  // A preview of another kind (e.g. just after switching) has a different shape: don't show it.
  const shown = preview && preview.kind === form.kind ? preview : null;

  // Live preview: the server computes everything (one source of truth for the math).
  useEffect(() => {
    if (!ready) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    const id = ++requestId.current;
    setChecking(true);
    const timer = setTimeout(() => {
      api
        .post("/api/forex-entries/preview", payload(form))
        .then((result) => id === requestId.current && (setPreview(result), setPreviewError(null)))
        .catch((e) => id === requestId.current && (setPreview(null), setPreviewError(e.message)))
        .finally(() => id === requestId.current && setChecking(false));
    }, 400);
    return () => clearTimeout(timer);
  }, [JSON.stringify(payload(form)), ready]);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      if (entry) await api.put(`/api/forex-entries/${entry.id}`, payload(form));
      else await api.post("/api/forex-entries", payload(form));
      toast(entry ? `${Noun} updated` : `${Noun} saved`);
      onSaved();
    } catch (e) {
      setSaveError(e.message);
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
      await api.del(`/api/forex-entries/${entry.id}`);
      toast(`${entry.kind === "dividend" ? "Dividend" : "Conversion"} deleted`);
      onSaved();
    } catch (e) {
      setSaveError(e.message);
    }
  };

  const refresh = async () => {
    try {
      const updated = await api.post(`/api/forex-entries/${entry.id}/refresh-rates`);
      setPreview(updated);
      toast(updated.kind === "dividend" ? "Market rate refreshed" : "Rates refreshed");
    } catch (e) {
      setSaveError(e.message);
    }
  };

  const needsBankRate =
    !dividend &&
    form.bank_id &&
    (form.bank_rate || (bank && !bank.auto) || (shown && shown.bank.rate == null && !checking));

  const footer = html`
    ${entry &&
    html`<button class=${`btn btn-ghost btn-danger btn-sm`} onClick=${remove}>
      <${Icon} name="trash" size=${14} />${confirmDelete ? "Click again to delete" : "Delete"}
    </button>`}
    ${entry &&
    html`<button
      class="btn btn-ghost btn-sm"
      onClick=${refresh}
      title=${entry.kind === "dividend" ? "Fetch the market rate again" : "Fetch the market and bank rates again"}
    >
      <${Icon} name="refresh" size=${14} />Refresh rates
    </button>`}
    <span class="spacer" />
    <button class="btn" onClick=${onClose}>Cancel</button>
    <button class="btn btn-primary" onClick=${save} disabled=${!ready || saving || Boolean(previewError)}>
      ${saving ? html`<${Spinner} />` : null}${entry ? "Save changes" : `Save ${noun}`}
    </button>
  `;

  return html`<${Drawer} title=${`${entry ? "Edit" : "New"} ${noun}`} onClose=${onClose} footer=${footer}>
    <div class="stack">
      <${Segmented}
        block
        value=${form.kind}
        onChange=${set("kind")}
        options=${[
          { value: "deposit", label: "INR → USD" },
          { value: "withdrawal", label: "USD → INR" },
          { value: "dividend", label: "Dividend" },
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
          id="occurred-at"
          label="Date & time (IST)"
          aside=${html`<button type="button" class="link field-hint" onClick=${() => set("occurred")(nowIstInput())}>Now</button>`}
        >
          <input
            id="occurred-at"
            class="input"
            type="datetime-local"
            value=${form.occurred}
            max=${nowIstInput()}
            onInput=${(e) => set("occurred")(e.target.value)}
          />
        </${Field}>
      </div>

      ${dividend
        ? html`
            <div class="row">
              <${Field} label="Ticker" hint="The stock or ETF that paid the dividend">
                <input
                  class="input"
                  value=${form.ticker}
                  maxlength="15"
                  autocomplete="off"
                  spellcheck="false"
                  autocapitalize="characters"
                  placeholder="e.g. VOO"
                  autofocus=${!entry}
                  onInput=${(e) => {
                    const value = e.target.value.toUpperCase().replace(/\s/g, "");
                    if (value !== e.target.value) e.target.value = value;
                    set("ticker")(value);
                  }}
                />
              </${Field}>
              <${Field} label="Dividend before tax" hint="Gross amount, as on your statement">
                <${MoneyInput} prefix="$" value=${form.gross} onInput=${setGross} />
              </${Field}>
            </div>
            <div class="row">
              <${Field}
                id="withheld-usd"
                label="US tax withheld"
                hint="India–US tax treaty rate for residents: 25%. Claimable as foreign tax credit (Form 67)."
                aside=${form.withheldEdited &&
                positive(form.gross) &&
                form.withheld !== quarterOf(form.gross) &&
                html`<button type="button" class="link field-hint" onClick=${resetWithheld}>Use 25%</button>`}
              >
                <${MoneyInput} id="withheld-usd" prefix="$" value=${form.withheld} onInput=${setWithheld} />
              </${Field}>
              <div class="field">
                <span class="field-label">You received</span>
                <div class="dividend-net">
                  ${ready && shown
                    ? usd(shown.usd_amount)
                    : ready && checking && !previewError
                      ? html`<span class="skeleton" style="height:16px;width:72px" />`
                      : html`<span class="subtle">—</span>`}
                </div>
                <span class="field-hint">Credited to your wallet after US tax</span>
              </div>
            </div>
          `
        : html`
          <div class="row">
            ${deposit
              ? html`
                  <${Field} label="Debited from bank" hint=${positive(form.inr) ? `${inr(form.inr)} left your account` : "Total ₹ that left your account"}>
                    <${MoneyInput} prefix="₹" value=${form.inr} onInput=${set("inr")} autofocus=${!entry} />
                  </${Field}>
                  <${Field} label="Credited to wallet" hint=${positive(form.usd) ? `${usd(form.usd)} arrived` : "$ that arrived"}>
                    <${MoneyInput} prefix="$" value=${form.usd} onInput=${set("usd")} />
                  </${Field}>
                `
              : html`
                  <${Field} label="Withdrawn from wallet" hint=${positive(form.usd) ? `${usd(form.usd)} left the wallet` : "$ that left the wallet"}>
                    <${MoneyInput} prefix="$" value=${form.usd} onInput=${set("usd")} autofocus=${!entry} />
                  </${Field}>
                  <${Field} label="Received in bank" hint=${positive(form.inr) ? `${inr(form.inr)} arrived` : "₹ that arrived"}>
                    <${MoneyInput} prefix="₹" value=${form.inr} onInput=${set("inr")} />
                  </${Field}>
                `}
          </div>

          <div>
            <${Disclosure} label="Charges and taxes" open=${showCharges} onToggle=${() => setShowCharges(!showCharges)} />
            ${showCharges &&
            html`<div class="row" style="margin-top:12px">
              <${Field}
                label="GST & charges"
                hint=${deposit ? "Part of the amount debited" : "Deducted before it reached you"}
              >
                <${MoneyInput} prefix="₹" value=${form.charges} onInput=${set("charges")} />
              </${Field}>
              ${deposit &&
              html`<${Field} label="TCS" hint="Part of the amount debited. Claimable in your ITR, so not a cost">
                <${MoneyInput} prefix="₹" value=${form.tcs} onInput=${set("tcs")} />
              </${Field}>`}
            </div>`}
          </div>

          <div class="row">
            <${Field} label="Compare with bank">
              <${Select}
                value=${form.bank_id}
                onChange=${set("bank_id")}
                options=${[
                  ...meta.banks.map((b) => ({ value: b.id, label: b.name })),
                  { value: "", label: "Don't compare" },
                ]}
              />
            </${Field}>
            ${needsBankRate &&
            html`<${Field}
              label=${`${bank?.name ?? "Bank"} ${deposit ? "TT selling" : "TT buying"} rate`}
              hint=${bank && !bank.auto && !form.bank_rate
                ? "Not fetched automatically. Copy it from the bank's rate sheet"
                : form.bank_rate
                  ? "Entered by you"
                  : "No published rate found for this date"}
            >
              <${MoneyInput} prefix="₹" value=${form.bank_rate} onInput=${set("bank_rate")} decimals=${4} placeholder="e.g. 96.50" />
            </${Field}>`}
          </div>

          `}

      <div>
        <${Disclosure} label="Advanced" open=${showAdvanced} onToggle=${() => setShowAdvanced(!showAdvanced)} />
        ${showAdvanced &&
        html`<div class="stack" style="margin-top:12px">
          <${Field}
            label="Market rate override"
            hint="Leave empty to use the market rate for this exact minute automatically"
          >
            <${MoneyInput} prefix="₹" value=${form.market_rate} onInput=${set("market_rate")} decimals=${4} placeholder="Automatic" />
          </${Field}>
          <${Field} label="Note">
            <textarea class="textarea" maxlength="500" value=${form.note} onInput=${(e) => set("note")(e.target.value)} />
          </${Field}>
        </div>`}
      </div>

      ${dividend
        ? html`<${DividendResult} preview=${ready ? shown : null} error=${previewError} checking=${checking} ready=${ready} />`
        : html`<${ResultPanel}
            preview=${ready ? shown : null}
            error=${previewError}
            checking=${checking}
            ready=${ready}
            meta=${meta}
            deposit=${deposit}
          />`}
      ${saveError && html`<div class="callout error"><${Icon} name="alert" />${saveError}</div>`}
    </div>
  </${Drawer}>`;
}

function ResultPanel({ preview, error, checking, ready, meta, deposit }) {
  if (!ready) {
    return html`<div class="callout">
      <${Icon} name="info" />Enter both amounts to see the rate you got and what it really cost.
    </div>`;
  }
  if (error) return html`<div class="callout error"><${Icon} name="alert" />${error}</div>`;
  if (!preview) return ResultSkeleton();

  const a = preview.analysis;
  const m = preview.market;
  const vs = a.vs_bank_inr;
  const bank = preview.bank_id && bankName(meta, preview.bank_id);
  const notes = marketNotes(m, "the markup is approximate");
  if (!isZero(preview.tcs_inr)) notes.push(`TCS of ${inr(preview.tcs_inr)} isn't counted as a cost. You can claim it in your ITR.`);
  if (isNegative(a.markup_inr)) notes.push("You got a better rate than the market benchmark. This can happen if the exact time is off.");
  if (vs != null) notes.push(`The ${bank} comparison is rate only. Sending through a bank directly would also add GST and a wire fee.`);

  return html`<section class="result" aria-live="polite">
    <div class="result-head">
      <span class="label">Hidden cost</span>
      ${checking ? html`<span class="subtle"><${Spinner} size=${13} /></span>` : null}
    </div>
    <div class="result-big">${inr(a.hidden_cost_inr)}<small>${pct(a.markup_pct, 3)} markup</small></div>
    <div class="result-lines">
      <div class="line"><span class="k">Rate you got</span><span>${rate(a.rate_got, 4)}</span></div>
      <${MarketLine} market=${m} />
      <div class="line">
        <span class="k">${deposit ? "₹ converted" : "₹ before charges"}</span><span>${inr(a.converted_inr)}</span>
      </div>
      <div class="line">
        <span class="k">${usd(preview.usd_amount)} at market rate</span><span>${inr(a.market_inr)}</span>
      </div>
      <div class="line"><span class="k">Exchange-rate markup</span><span>${inr(a.markup_inr)}</span></div>
      ${!isZero(preview.charges_inr) &&
      html`<div class="line"><span class="k">GST & charges</span><span>${inr(preview.charges_inr)}</span></div>`}
      <div class="line total"><span>Hidden cost</span><span>${inr(a.hidden_cost_inr)}</span></div>
    </div>
    ${bank &&
    html`<div class="result-lines">
      ${vs == null
        ? html`<div class="line"><span class="k">vs ${bank}</span><span class="subtle">No rate to compare</span></div>`
        : html`
            <div class="line">
              <span class="k">${bank} ${deposit ? "TT selling" : "TT buying"} rate${preview.bank.manual ? " (entered)" : ""}</span>
              <span>${rate(preview.bank.rate, 4)}</span>
            </div>
            <div class="line">
              <span class="k">${isNegative(vs) ? `${bank}'s rate would have been better by` : `Better than ${bank}'s rate by`}</span>
              <span class=${isZero(vs) ? "" : isNegative(vs) ? "neg" : "pos"}>${inr(String(vs).replace("-", ""))}</span>
            </div>
          `}
    </div>`}
    ${notes.length > 0 &&
    html`<div class="result-note"><${Icon} name="info" size=${14} /><div>${notes.map((n) => html`<div>${n}</div>`)}</div></div>`}
  </section>`;
}

function ResultSkeleton() {
  return html`<div class="result" style="padding:16px">
    <div class="skeleton" style="height:14px;width:30%" />
    <div class="skeleton" style="height:30px;width:45%;margin-top:10px" />
    <div class="skeleton" style="height:12px;width:100%;margin-top:18px" />
    <div class="skeleton" style="height:12px;width:100%;margin-top:10px" />
  </div>`;
}

function MarketLine({ market: m }) {
  return html`<div class="line">
    <span class="k">Market rate · ${m.source === "manual" ? "entered" : `${sourceLabel(m.source)}, ${intervalLabel(m.interval)}`}</span>
    <span>${rate(m.rate, 4)}</span>
  </div>`;
}

function marketNotes(m, approximate) {
  if (m.source === "manual") return ["Using the market rate you entered."];
  if (m.stale)
    return [
      `No live market quote at this exact time (USD/INR data mostly covers Indian market hours, about 9:00 to 15:30 IST on weekdays). Compared with the last quote, from ${formatShort(m.bar_at)} IST.`,
    ];
  if (m.interval !== "1m") return [`Only ${intervalLabel(m.interval)} market data is available this far back, so ${approximate}.`];
  return [];
}

function DividendResult({ preview, error, checking, ready }) {
  if (!ready) {
    return html`<div class="callout">
      <${Icon} name="info" />Enter the ticker and the dividend before tax to see what you received and its value in rupees.
    </div>`;
  }
  if (error) return html`<div class="callout error"><${Icon} name="alert" />${error}</div>`;
  if (!preview) return ResultSkeleton();

  const m = preview.market;
  const notes = marketNotes(m, "the rupee values are approximate");
  notes.push(
    "Rupee values use the market rate. Your tax return uses SBI's TT buying rate (Rule 115), so the taxable amount can differ slightly.",
  );

  return html`<section class="result" aria-live="polite">
    <div class="result-head">
      <span class="label">Income in rupees</span>
      ${checking ? html`<span class="subtle"><${Spinner} size=${13} /></span>` : null}
    </div>
    <div class="result-big">${inr(preview.income_inr)}<small>${preview.ticker} dividend after US tax</small></div>
    <div class="result-lines">
      <div class="line"><span class="k">Dividend before tax</span><span>${usd(preview.gross_usd)}</span></div>
      <div class="line"><span class="k">US tax withheld</span><span>${usd(preview.withheld_usd)}</span></div>
      <div class="line total"><span>Net received</span><span>${usd(preview.usd_amount)}</span></div>
    </div>
    <div class="result-lines">
      <${MarketLine} market=${m} />
      <div class="line"><span class="k">Income in rupees</span><span>${inr(preview.income_inr)}</span></div>
      <div class="line"><span class="k">Before US tax</span><span>${inr(preview.gross_inr)}</span></div>
    </div>
    <div class="result-note"><${Icon} name="info" size=${14} /><div>${notes.map((n) => html`<div>${n}</div>`)}</div></div>
  </section>`;
}
