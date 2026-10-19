import { render } from "preact";
import { useCallback, useEffect, useState } from "preact/hooks";
import { api } from "./api.js";
import { Icon } from "./components/icons.js";
import { Toaster } from "./components/ui.js";
import { html } from "./html.js";
import { MetaContext } from "./meta.js";
import { ConversionsPage } from "./pages/conversions.js";
import { RatesPage } from "./pages/rates.js";
import { SettingsPage } from "./pages/settings.js";

const ROUTES = {
  conversions: { label: "Conversions", icon: "arrows", page: ConversionsPage },
  rates: { label: "Rates", icon: "chart", page: RatesPage },
  settings: { label: "Settings", icon: "sliders", page: SettingsPage },
};

function currentRoute() {
  const name = location.hash.replace(/^#\/?/, "");
  return ROUTES[name] ? name : "conversions";
}

function App() {
  const [route, setRoute] = useState(currentRoute());
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);

  const reloadMeta = useCallback(() => api.get("/api/meta").then(setMeta).catch((e) => setError(e.message)), []);

  useEffect(() => {
    reloadMeta();
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    document.title = `${ROUTES[route].label} · Forex Ledger`;
  }, [route]);

  const Page = ROUTES[route].page;
  return html`<${MetaContext.Provider} value=${meta && { ...meta, reload: reloadMeta }}>
    <div class="app">
      <nav class="sidebar" aria-label="Main">
        <div class="brand">
          <div class="brand-mark"><${Icon} name="arrows" size=${14} strokeWidth=${2.2} /></div>
          <span>Forex Ledger</span>
        </div>
        <div class="nav">
          ${Object.entries(ROUTES).map(
            ([name, r]) => html`<a href=${`#/${name}`} class=${route === name ? "active" : ""}>
              <${Icon} name=${r.icon} />${r.label}
            </a>`,
          )}
          <div class="nav-section">Coming next</div>
          <a class="soon" aria-disabled="true"><${Icon} name="layers" />Stocks<span class="badge tag">Soon</span></a>
          <a class="soon" aria-disabled="true"><${Icon} name="calculator" />Sell calculator<span class="badge tag">Soon</span></a>
        </div>
        <div class="sidebar-foot">
          Data stays on this computer.<br />Not financial or tax advice.
        </div>
      </nav>
      <main class="main">
        ${error
          ? html`<div class="page"><div class="callout error"><${Icon} name="alert" />Couldn't reach the app server: ${error}</div></div>`
          : meta
            ? html`<${Page} />`
            : null}
      </main>
    </div>
    <${Toaster} />
  </${MetaContext.Provider}>`;
}

render(html`<${App} />`, document.getElementById("root"));
