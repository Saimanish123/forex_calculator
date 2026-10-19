import { useEffect, useState } from "preact/hooks";
import { html } from "../html.js";
import { Icon } from "./icons.js";

// With `aside` (e.g. a "Now" button) the label can't wrap the control — a click on the label would
// press the button — so pass `id` and give the control the same id.
export function Field({ label, hint, error, aside, id, children }) {
  const foot = error
    ? html`<span class="field-error">${error}</span>`
    : hint && html`<span class="field-hint">${hint}</span>`;
  if (aside) {
    return html`<div class="field">
      <div class="field-label"><label for=${id}>${label}</label>${aside}</div>
      ${children}${foot}
    </div>`;
  }
  return html`<label class="field">
    ${label && html`<span class="field-label">${label}</span>`}
    ${children}${foot}
  </label>`;
}

// Amount input that only accepts plain decimals (no exponent, no grouping) so the value sent to the
// server is exactly what the user typed.
export function MoneyInput({ prefix, suffix, value, onInput, placeholder = "0.00", autofocus, decimals = 2, ...rest }) {
  const handle = (event) => {
    const cleaned = event.target.value.replace(/,/g, "").replace(/[^\d.]/g, "");
    const [whole, ...fraction] = cleaned.split(".");
    const next = fraction.length ? `${whole}.${fraction.join("").slice(0, decimals)}` : whole;
    if (next !== event.target.value) event.target.value = next;
    onInput(next);
  };
  return html`<div class=${`input-group ${prefix ? "has-prefix" : ""} ${suffix ? "has-suffix" : ""}`}>
    ${prefix && html`<span class="prefix">${prefix}</span>`}
    ${suffix && html`<span class="suffix">${suffix}</span>`}
    <input
      class="input"
      inputmode="decimal"
      autocomplete="off"
      value=${value}
      placeholder=${placeholder}
      onInput=${handle}
      autofocus=${autofocus}
      ...${rest}
    />
  </div>`;
}

export function Select({ value, onChange, options, ...rest }) {
  return html`<select class="select" value=${value} onChange=${(e) => onChange(e.target.value)} ...${rest}>
    ${options.map((o) => html`<option value=${o.value}>${o.label}</option>`)}
  </select>`;
}

export function Segmented({ value, onChange, options, block }) {
  return html`<div class=${`seg ${block ? "block" : ""}`} role="tablist">
    ${options.map(
      (o) => html`<button
        type="button"
        role="tab"
        aria-selected=${value === o.value}
        class=${value === o.value ? "on" : ""}
        onClick=${() => onChange(o.value)}
      >
        ${o.label}
      </button>`,
    )}
  </div>`;
}

export function Stat({ label, value, sub, loading }) {
  return html`<div class="card stat">
    <div class="stat-label">${label}</div>
    ${loading
      ? html`<div class="skeleton" style="height:28px;width:60%;margin-top:8px" />`
      : html`<div class="stat-value">${value}</div>`}
    <div class="stat-sub">${sub || " "}</div>
  </div>`;
}

export function EmptyState({ icon, title, children, action }) {
  return html`<div class="empty">
    <div class="empty-icon"><${Icon} name=${icon} size=${18} /></div>
    <h3>${title}</h3>
    <p>${children}</p>
    ${action}
  </div>`;
}

export function Drawer({ title, onClose, children, footer }) {
  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);
  return html`<div>
    <div class="overlay" onClick=${onClose} />
    <aside class="drawer" role="dialog" aria-modal="true" aria-label=${title}>
      <header class="drawer-head">
        <h2>${title}</h2>
        <button class="btn btn-ghost btn-icon" onClick=${onClose} aria-label="Close">
          <${Icon} name="x" />
        </button>
      </header>
      <div class="drawer-body">${children}</div>
      ${footer && html`<footer class="drawer-foot">${footer}</footer>`}
    </aside>
  </div>`;
}

export function Disclosure({ label, open, onToggle }) {
  return html`<button type="button" class=${`disclosure ${open ? "open" : ""}`} onClick=${onToggle}>
    ${label}<${Icon} name="chevron" size=${14} />
  </button>`;
}

export function Spinner({ size = 14 }) {
  return html`<${Icon} name="loader" size=${size} class="spin" />`;
}

// ---- toasts ----------------------------------------------------------------------------

const listeners = new Set();
let nextId = 1;

export function toast(message, kind = "ok") {
  const item = { id: nextId++, message, kind };
  listeners.forEach((fn) => fn(item));
}

export function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const add = (item) => {
      setItems((list) => [...list, item]);
      setTimeout(() => setItems((list) => list.filter((i) => i.id !== item.id)), 3200);
    };
    listeners.add(add);
    return () => listeners.delete(add);
  }, []);
  return html`<div class="toasts" aria-live="polite">
    ${items.map(
      (t) => html`<div class=${`toast ${t.kind === "error" ? "error" : ""}`} key=${t.id}>
        <${Icon} name=${t.kind === "error" ? "alert" : "check"} size=${15} />${t.message}
      </div>`,
    )}
  </div>`;
}
