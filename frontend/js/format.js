// Display formatting only. Values arrive as exact decimal strings already rounded by the server;
// Intl formats them for display (Indian digit grouping for ₹).

const IST = "Asia/Kolkata";
const MINUS = "−";

const inrFormatters = {};
const usdFormatters = {};

function formatter(cache, locale, digits) {
  if (!cache[digits]) {
    cache[digits] = new Intl.NumberFormat(locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }
  return cache[digits];
}

function parts(value) {
  if (value === null || value === undefined || value === "") return null;
  const text = String(value);
  const negative = text.startsWith("-");
  return { negative, abs: negative ? text.slice(1) : text };
}

// Intl.NumberFormat accepts decimal strings directly, so no float rounding is introduced.
export function inr(value, digits = 2) {
  const p = parts(value);
  if (!p) return "—";
  return `${p.negative ? MINUS : ""}₹${formatter(inrFormatters, "en-IN", digits).format(p.abs)}`;
}

export function usd(value, digits = 2) {
  const p = parts(value);
  if (!p) return "—";
  return `${p.negative ? MINUS : ""}$${formatter(usdFormatters, "en-US", digits).format(p.abs)}`;
}

export function rate(value, digits = 2) {
  const p = parts(value);
  if (!p) return "—";
  return `₹${formatter(inrFormatters, "en-IN", digits).format(p.abs)}`;
}

export function pct(value, digits = 2) {
  const p = parts(value);
  if (!p) return "—";
  return `${p.negative ? MINUS : ""}${formatter(usdFormatters, "en-US", digits).format(p.abs)}%`;
}

export function signedInr(value, digits = 2) {
  const p = parts(value);
  if (!p) return "—";
  const sign = p.negative ? MINUS : isZero(p.abs) ? "" : "+";
  return `${sign}₹${formatter(inrFormatters, "en-IN", digits).format(p.abs)}`;
}

export const isNegative = (value) => parts(value)?.negative ?? false;
export const isZero = (value) => /^0*\.?0*$/.test(String(value).replace("-", ""));

const dateFmt = new Intl.DateTimeFormat("en-IN", { timeZone: IST, day: "numeric", month: "short", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false });
const shortFmt = new Intl.DateTimeFormat("en-IN", { timeZone: IST, weekday: "short", day: "numeric", month: "short" });

export const formatDate = (iso) => dateFmt.format(new Date(iso));
export const formatTime = (iso) => timeFmt.format(new Date(iso));
export const formatShort = (iso) => `${shortFmt.format(new Date(iso))}, ${timeFmt.format(new Date(iso))}`;
export const formatDay = (isoDate) => dateFmt.format(new Date(`${isoDate}T12:00:00+05:30`));

// <input type="datetime-local"> works in browser-local time; the app always means IST.
export function toIstInput(iso) {
  const d = new Date(iso);
  const ist = new Date(d.getTime() + (330 + d.getTimezoneOffset()) * 60000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${ist.getFullYear()}-${pad(ist.getMonth() + 1)}-${pad(ist.getDate())}T${pad(ist.getHours())}:${pad(ist.getMinutes())}`;
}

export const fromIstInput = (value) => (value ? `${value}:00+05:30` : null);
export const nowIstInput = () => toIstInput(new Date().toISOString());
export const todayIst = () => nowIstInput().slice(0, 10);

const INTERVAL_LABEL = { "1m": "1-minute", "5m": "5-minute", "1h": "hourly", "1d": "daily" };
const SOURCE_LABEL = { yahoo: "Yahoo Finance", frankfurter: "ECB reference", open_er: "ExchangeRate-API", manual: "Entered by you" };

export const intervalLabel = (interval) => INTERVAL_LABEL[interval] || "";
export const sourceLabel = (source) => SOURCE_LABEL[source] || source;
