import { useState } from "preact/hooks";
import { api } from "../api.js";
import { Icon } from "../components/icons.js";
import { Field, MoneyInput, Select, Spinner, toast } from "../components/ui.js";
import { html } from "../html.js";
import { useMeta } from "../meta.js";

const KIND_LABEL = { gift_city: "GIFT City platform", us_broker: "US broker", bank_lrs: "Direct bank transfer" };

export function SettingsPage() {
  const meta = useMeta();
  return html`<div class="page">
    <header class="page-header">
      <div>
        <h1>Settings</h1>
        <p>Defaults, platform fees, banks and your data.</p>
      </div>
    </header>
    <${Defaults} meta=${meta} />
    <${Platforms} meta=${meta} />
    <${Banks} meta=${meta} />
    <${DataSection} meta=${meta} />
  </div>`;
}

function Section({ title, description, children }) {
  return html`<section class="settings-section">
    <div><h2>${title}</h2><p>${description}</p></div>
    <div>${children}</div>
  </section>`;
}

function Defaults({ meta }) {
  const update = async (key, value, label) => {
    try {
      await api.put("/api/settings", { [key]: value });
      await meta.reload();
      toast(`${label} updated`);
    } catch (e) {
      toast(e.message, "error");
    }
  };
  return html`<${Section}
    title="Defaults"
    description="Pre-filled on every new entry. You can still change them per entry."
  >
    <div class="row" style="max-width:560px">
      <${Field} label="Default platform" hint="Where you usually invest">
        <${Select}
          value=${meta.settings.default_platform}
          onChange=${(v) => update("default_platform", v, "Default platform")}
          options=${meta.platforms.map((p) => ({ value: p.id, label: p.name }))}
        />
      </${Field}>
      <${Field} label="Default bank" hint="Used to compare every conversion">
        <${Select}
          value=${meta.settings.default_bank}
          onChange=${(v) => update("default_bank", v, "Default bank")}
          options=${meta.banks.map((b) => ({ value: b.id, label: b.name }))}
        />
      </${Field}>
    </div>
  </${Section}>`;
}

const FEE_FIELDS = [
  { key: "brokerage_pct", label: "Brokerage", suffix: "%", decimals: 4 },
  { key: "brokerage_min_usd", label: "Minimum per trade", prefix: "$" },
  { key: "brokerage_max_usd", label: "Maximum per trade", prefix: "$", placeholder: "No cap" },
  { key: "brokerage_per_share_usd", label: "Per share", prefix: "$", decimals: 4 },
  { key: "brokerage_flat_usd", label: "Flat per trade", prefix: "$" },
  { key: "brokerage_gst_pct", label: "GST on brokerage", suffix: "%" },
  { key: "sell_fee_usd", label: "Other fees per sell", prefix: "$" },
  { key: "transfer_fee_inr", label: "Transfer fee", prefix: "₹" },
  { key: "expected_markup_pct", label: "Expected forex markup", suffix: "%", decimals: 3 },
];

function Platforms({ meta }) {
  const [open, setOpen] = useState(null); // platform id or "new"
  return html`<${Section}
    title="Platforms"
    description="Fee rules pre-fill your trades. The actual figures from your statement always take priority."
  >
    <div class="list">
      ${meta.platforms.map(
        (p) => html`<div class="list-item" key=${p.id}>
          <div class="list-row">
            <div class="grow">
              <div style="font-weight:500">
                ${p.name}${p.id === meta.settings.default_platform && html` <span class="badge solid">Default</span>`}
              </div>
              <div class="cell-sub">${KIND_LABEL[p.kind]} · ${feeSummary(p)}</div>
            </div>
            <button class="btn btn-sm" onClick=${() => setOpen(open === p.id ? null : p.id)}>
              ${open === p.id ? "Close" : "Edit fees"}
            </button>
          </div>
          ${open === p.id && html`<${PlatformForm} platform=${p} meta=${meta} onDone=${() => setOpen(null)} />`}
        </div>`,
      )}
      <div class="list-item">
        ${open === "new"
          ? html`<${PlatformForm} meta=${meta} onDone=${() => setOpen(null)} />`
          : html`<button class="btn btn-sm btn-ghost" onClick=${() => setOpen("new")}>
              <${Icon} name="plus" size=${14} />Add platform
            </button>`}
      </div>
    </div>
  </${Section}>`;
}

function feeSummary(p) {
  const parts = [];
  if (Number(p.brokerage_pct)) parts.push(`${trim(p.brokerage_pct)}% brokerage${p.brokerage_max_usd ? ` (max $${trim(p.brokerage_max_usd)})` : ""}`);
  if (Number(p.brokerage_per_share_usd)) parts.push(`$${trim(p.brokerage_per_share_usd)}/share`);
  if (Number(p.brokerage_flat_usd)) parts.push(`$${trim(p.brokerage_flat_usd)}/trade`);
  if (!parts.length) parts.push("no brokerage set");
  parts.push(`~${trim(p.expected_markup_pct)}% forex markup`);
  return parts.join(" · ");
}

const trim = (value) => String(value).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");

function PlatformForm({ platform, meta, onDone }) {
  const blank = Object.fromEntries(FEE_FIELDS.map((f) => [f.key, ""]));
  const [form, setForm] = useState(
    platform
      ? { name: platform.name, kind: platform.kind, ...Object.fromEntries(FEE_FIELDS.map((f) => [f.key, platform[f.key] ?? ""])) }
      : { name: "", kind: "us_broker", ...blank, expected_markup_pct: "1" },
  );
  const [saving, setSaving] = useState(false);
  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));

  const save = async () => {
    setSaving(true);
    const body = { ...form };
    FEE_FIELDS.forEach((f) => {
      if (body[f.key] === "") body[f.key] = f.key === "brokerage_max_usd" ? null : "0";
    });
    try {
      if (platform) await api.put(`/api/platforms/${platform.id}`, body);
      else await api.post("/api/platforms", body);
      await meta.reload();
      toast(platform ? `${form.name} updated` : `${form.name} added`);
      onDone();
    } catch (e) {
      toast(e.message, "error");
      setSaving(false);
    }
  };

  return html`<div style="margin-top:${platform ? 14 : 0}px">
    <div class="row">
      <${Field} label="Name"><input class="input" value=${form.name} onInput=${(e) => set("name")(e.target.value)} /></${Field}>
      <${Field} label="Type">
        <${Select}
          value=${form.kind}
          onChange=${set("kind")}
          options=${Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }))}
        />
      </${Field}>
    </div>
    <div class="fee-grid">
      ${FEE_FIELDS.map(
        (f) => html`<${Field} label=${f.label} key=${f.key}>
          <${MoneyInput}
            prefix=${f.prefix}
            suffix=${f.suffix}
            decimals=${f.decimals ?? 2}
            placeholder=${f.placeholder ?? "0"}
            value=${form[f.key]}
            onInput=${set(f.key)}
          />
        </${Field}>`,
      )}
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
      <button class="btn" onClick=${onDone}>Cancel</button>
      <button class="btn btn-primary" onClick=${save} disabled=${saving || !form.name.trim()}>
        ${saving && html`<${Spinner} />`}${platform ? "Save fees" : "Add platform"}
      </button>
    </div>
  </div>`;
}

function Banks({ meta }) {
  const [name, setName] = useState("");
  const add = async () => {
    try {
      await api.post("/api/banks", { name });
      await meta.reload();
      toast(`${name} added`);
      setName("");
    } catch (e) {
      toast(e.message, "error");
    }
  };
  return html`<${Section}
    title="Banks"
    description="Banks you compare against. SBI, ICICI and HDFC rates are fetched automatically; for others you enter the rate from the bank's rate sheet."
  >
    <div class="list">
      ${meta.banks.map(
        (b) => html`<div class="list-item list-row" key=${b.id}>
          <span class="grow" style="font-weight:500">
            ${b.name}${b.id === meta.settings.default_bank && html` <span class="badge solid">Default</span>`}
          </span>
          <span class="badge">${b.auto ? "Automatic" : "Manual"}</span>
        </div>`,
      )}
      <div class="list-item list-row">
        <input
          class="input grow"
          placeholder="Add another bank, e.g. Yes Bank"
          value=${name}
          onInput=${(e) => setName(e.target.value)}
          onKeyDown=${(e) => e.key === "Enter" && name.trim() && add()}
        />
        <button class="btn" onClick=${add} disabled=${!name.trim()}>Add bank</button>
      </div>
    </div>
  </${Section}>`;
}

function DataSection({ meta }) {
  const [backupDir, setBackupDir] = useState(meta.settings.backup_dir || "");
  const [busy, setBusy] = useState(false);

  const saveDir = async () => {
    try {
      await api.put("/api/settings", { backup_dir: backupDir });
      await meta.reload();
      toast("Backup folder saved");
    } catch (e) {
      toast(e.message, "error");
    }
  };
  const backupNow = async () => {
    setBusy(true);
    try {
      const result = await api.post("/api/backup");
      toast(`Backed up to ${result.path.split("/").pop()}`);
    } catch (e) {
      toast(e.message, "error");
    }
    setBusy(false);
  };

  return html`<${Section}
    title="Your data"
    description="Everything is stored in one file on this computer. A backup is made every time the app starts (the last 30 are kept)."
  >
    <div class="stack" style="max-width:560px">
      <${Field} label="Database file"><div class="input mono" style="display:flex;align-items:center;overflow-x:auto;white-space:nowrap">${meta.database}</div></${Field}>
      <${Field}
        label="Extra backup folder"
        hint="Optional. For example a folder in iCloud Drive or Google Drive, so a copy survives if this computer doesn't."
      >
        <div class="list-row">
          <input class="input grow mono" placeholder="~/Library/Mobile Documents/com~apple~CloudDocs/Forex Ledger" value=${backupDir} onInput=${(e) => setBackupDir(e.target.value)} />
          <button class="btn" onClick=${saveDir} disabled=${backupDir === (meta.settings.backup_dir || "")}>Save</button>
        </div>
      </${Field}>
      <div><button class="btn" onClick=${backupNow} disabled=${busy}>${busy ? html`<${Spinner} />` : html`<${Icon} name="check" />`}Back up now</button></div>
    </div>
  </${Section}>`;
}
