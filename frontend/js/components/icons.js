// Icon paths adapted from Lucide (ISC License, https://lucide.dev).
import { html } from "../html.js";

const PATHS = {
  arrows: html`<path d="m16 3 4 4-4 4" /><path d="M20 7H4" /><path d="m8 21-4-4 4-4" /><path d="M4 17h16" />`,
  plus: html`<path d="M5 12h14" /><path d="M12 5v14" />`,
  x: html`<path d="M18 6 6 18" /><path d="m6 6 12 12" />`,
  chart: html`<path d="M22 7 13.5 15.5 8.5 10.5 2 17" /><path d="M16 7h6v6" />`,
  sliders: html`<path d="M20 7h-9" /><path d="M14 17H5" /><circle cx="17" cy="17" r="3" /><circle cx="7" cy="7" r="3" />`,
  refresh: html`<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" />`,
  trash: html`<path d="M3 6h18" /><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" /><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />`,
  chevron: html`<path d="m6 9 6 6 6-6" />`,
  info: html`<circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" />`,
  alert: html`<circle cx="12" cy="12" r="10" /><path d="M12 8v4" /><path d="M12 16h.01" />`,
  check: html`<path d="M20 6 9 17l-5-5" />`,
  clock: html`<circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" />`,
  bank: html`<path d="M3 22h18" /><path d="M6 18v-7" /><path d="M10 18v-7" /><path d="M14 18v-7" /><path d="M18 18v-7" /><path d="M12 2 20 7H4z" />`,
  layers: html`<path d="m12 2 10 5-10 5L2 7z" /><path d="m2 17 10 5 10-5" /><path d="m2 12 10 5 10-5" />`,
  calculator: html`<rect width="16" height="20" x="4" y="2" rx="2" /><path d="M8 6h8" /><path d="M16 14v4" /><path d="M16 10h.01" /><path d="M12 10h.01" /><path d="M8 10h.01" /><path d="M12 14h.01" /><path d="M8 14h.01" /><path d="M12 18h.01" /><path d="M8 18h.01" />`,
  loader: html`<path d="M21 12a9 9 0 1 1-6.22-8.56" />`,
};

export function Icon({ name, size = 16, class: className = "", strokeWidth = 1.75 }) {
  return html`<svg
    class=${className}
    width=${size}
    height=${size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width=${strokeWidth}
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >${PATHS[name]}</svg>`;
}
