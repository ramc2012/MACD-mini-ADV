// The market-intelligence console inside the app: every MI section (overview, sectors, market data, NSE, macro,
// platform/brokers) in its app-embed mode, which drops MI's own title and theme switch and keeps its tabs.
import { MI_URL } from "./runtime";

export function MarketIntelligencePage({ section, sub }: { section?: string; sub?: string }) {
  const query = new URLSearchParams({ embed: "app", theme: "dark", ...(section ? { section } : {}), ...(sub ? { sub } : {}) });
  return <section className="ledger-page mi-page">
    <div className="page-heading"><div><h1>Market intelligence</h1><p>The shared platform: breadth, sectors, chains, options, NSE, macro and broker logins · <a href={`${MI_URL}/`} target="_blank" rel="noopener noreferrer">open in its own tab ↗</a></p></div></div>
    <iframe title="Market intelligence" src={`${MI_URL}/?${query}`} />
  </section>;
}
