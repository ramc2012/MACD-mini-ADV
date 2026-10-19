import { useEffect, useRef, useState } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";

type Leg = { n: number; delta: number };
export type BookGreek = {
  positions: number; priced: number; unpriced: string[]; premium_value: number; equity: number;
  net_delta: number; gross_delta: number; theta_day: number; vega_point: number; one_pct_down: number;
  net_delta_pct_equity: number | null; theta_pct_equity: number | null;
  calls: Leg; puts: Leg; by_underlying: { root: string; net_delta: number }[];
};
type Payload = { as_of: string; model: string; books: Record<string, BookGreek> };

const lakh = (value: number) => `₹${(value / 1e5).toLocaleString("en-IN", { maximumFractionDigits: 1 })} L`;
const pct = (value: number | null) => (value === null || !Number.isFinite(value) ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`);

/** Delta-adjusted exposure of every paper book. Display only: no entry or exit reads it. */
export function BookGreeks() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const controller = new AbortController();
    const load = () => void requestJson<Payload>(`${API_URL}/api/risk/greeks`, { headers, signal: controller.signal })
      .then((payload) => { setData(payload); setError(""); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unavailable"); });
    load();
    const timer = setInterval(load, 60_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  const books = Object.entries(data?.books || {}).filter(([, book]) => book.positions > 0);
  const details = useRef<HTMLDetailsElement>(null);
  useEffect(() => { if (details.current) details.current.open = true; }, []);
  return <details className="portfolio-risk" ref={details}>
    <summary>Book Greeks · net delta, theta and vega of open options{data ? ` · ${new Date(data.as_of).toLocaleTimeString("en-IN")}` : ""}</summary>
    <p>Delta is in rupees of underlying (delta × quantity × spot); "1% down" is the delta-only loss if every underlying falls 1%. {data?.model}. {error && `Unavailable: ${error}`}</p>
    <div className="risk-table"><table className="ledger-table"><thead><tr>
      <th>Book</th><th>Positions (CE / PE)</th><th>Net delta</th><th>% of equity</th><th>Gross |delta|</th>
      <th>1% down</th><th>Theta / day</th><th>Vega / IV pt</th><th>Largest net delta</th>
    </tr></thead><tbody>{books.map(([name, book]) => <tr key={name}>
      <td>{name}</td>
      <td>{book.positions} ({book.calls.n} / {book.puts.n}){book.unpriced.length ? ` · ${book.unpriced.length} unpriced` : ""}</td>
      <td>{lakh(book.net_delta)}</td>
      <td>{pct(book.net_delta_pct_equity)}</td>
      <td>{lakh(book.gross_delta)}</td>
      <td>{lakh(book.one_pct_down)}</td>
      <td>{lakh(book.theta_day)} ({pct(book.theta_pct_equity)})</td>
      <td>{lakh(book.vega_point)}</td>
      <td>{book.by_underlying.slice(0, 3).map((row) => `${row.root} ${lakh(row.net_delta)}`).join(", ")}</td>
    </tr>)}</tbody></table></div>
  </details>;
}
