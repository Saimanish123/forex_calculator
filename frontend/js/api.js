// Thin JSON client. Amounts travel as decimal strings and are never parsed for arithmetic here —
// all money math happens on the server.

async function request(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(data) || `Request failed (${response.status})`);
  return data;
}

function errorMessage(data) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0];
    const field = first.loc ? first.loc[first.loc.length - 1] : "";
    return field ? `${humanize(field)}: ${first.msg}` : first.msg;
  }
  return null;
}

const humanize = (key) => String(key).replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  put: (path, body) => request("PUT", path, body),
  del: (path) => request("DELETE", path),
};
