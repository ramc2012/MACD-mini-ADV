import { useCallback, useEffect, useMemo, useState } from "react";
import { rupees, tone } from "./agentMath";
import { ReturnsChart } from "./LaneEquityChart";
import { API_TOKEN, API_URL } from "./runtime";

// Every paper book scored alike (engine scoreboard.py, /api/scoreboard).
type Stats = { trades: number; wins: number; win_rate: number | null; net: number; profit_factor: number | null; avg_win: number | null; avg_loss: number | null };
type ExitRow = Stats & { reason: string; hold: string };
type Book = {
  lane: string; label: string; error?: string; capital: number; equity: number; cash: number; return_pct: number | null; realized: number;
  unrealized: number; open_positions: number; max_drawdown: number; max_drawdown_pct: number; closed: Stats;
  weekly: { week: string; net: number }[]; best_week: { week: string; net: number } | null; net_without_best_week: number | null;
  exits: ExitRow[]; book_full: boolean | null; ticket: number | null; slots: number | null; note: string | null; since: string | null;
  curve?: { at: string; equity: number }[];
};
type Board = { as_of: string; lanes: Book[]; hold_buckets: string[] };

const headers = (): HeadersInit => (API_TOKEN ? { "x-macd-token": API_TOKEN } : {});
const lakh = (value: number) => Math.abs(value) >= 10_000_000 ? `₹${(value / 10_000_000).toFixed(2)} Cr` : `₹${(value / 100_000).toFixed(2)} L`;
const percent = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
const day = (iso: string | null | undefined) => iso ? new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" }).format(new Date(iso)) : "—";
const stamp = (iso: string | null | undefined) => iso ? `${new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(new Date(iso))} IST` : "—";
const pf = (value: number | null | undefined) => value == null ? "—" : value.toFixed(2);
const win = (value: number | null | undefined) => value == null ? "—" : `${Math.round(value * 100)}%`;
const words = (text: string) => text.replace(/_/g, " ").toLowerCase();

export function ScoreboardPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [selected, setSelected] = useState("macd");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (refresh = false) => {
    try {
      const response = await fetch(`${API_URL}/api/scoreboard${refresh ? "?refresh=true" : ""}`, { headers: headers() });
      if (!response.ok) throw new Error(`/api/scoreboard failed (HTTP ${response.status})`);
      setBoard(await response.json()); setError(null);
    } catch (exc) { setError(String(exc instanceof Error ? exc.message : exc)); }
  }, []);
  useEffect(() => { void load(); const id = window.setInterval(() => void load(), 60_000); return () => window.clearInterval(id); }, [load]);

  const books = useMemo(() => (board?.lanes ?? []).filter((b) => !b.error), [board]);
  const broken = (board?.lanes ?? []).filter((b) => b.error);
  const weeks = useMemo(() => [...new Set(books.flatMap((b) => b.weekly.map((w) => w.week)))].sort(), [books]);
  const book = books.find((b) => b.lane === selected) ?? books[0];
  const holds = board?.hold_buckets ?? [];
  const returnBooks = useMemo(() => books.map((b) => ({ lane: b.lane, label: b.label, capital: b.capital, points: b.curve ?? [] })), [books]);
  const reasons = useMemo(() => {
    if (!book) return [];
    const byReason = new Map<string, Record<string, ExitRow>>();
    for (const row of book.exits) byReason.set(row.reason, { ...(byReason.get(row.reason) ?? {}), [row.hold]: row });
    return [...byReason.entries()].map(([reason, cells]) => ({ reason, cells, net: Object.values(cells).reduce((s, c) => s + c.net, 0),
      trades: Object.values(cells).reduce((s, c) => s + c.trades, 0) })).sort((a, b) => a.net - b.net);
  }, [book]);

  const head = <div className="agent-head panel-row">
    <div><b>Lanes scoreboard</b><span className="agent-sub">every paper book, same measures{board ? ` · as of ${stamp(board.as_of)}` : ""}</span></div>
    <div className="agent-actions"><button className="settings-button" onClick={() => void load(true)}>Refresh</button></div>
  </div>;
  // Until the first answer arrives there is nothing to tabulate: say so instead of drawing empty tables.
  if (!board) return <div className="agent-page lanes-page">
    {head}
    {error ? <div className="agent-error" role="alert">{error}</div>
      : <div className="agent-empty state-loading" role="status">Loading the scoreboard…</div>}
  </div>;

  return <div className="agent-page lanes-page">
    {head}
    <section className="panel agent-panel">
      <details><summary>ⓘ How the numbers are made</summary><p>A trade runs from its first entry until the position is flat; partial exits count toward weekly P&amp;L and the exit table, not toward win rate or PF. Weeks are IST weeks (Monday) of the exit. "Without best week" removes the single best week to show how much rests on it. Drawdown is the largest fall from a running peak over the whole stored equity curve. Book full: every slot is used or cash is below one ticket. Paper fills; costs as each lane records them. Short samples are not proof either way.</p></details>
    </section>
    {error && <div className="agent-error">{error}</div>}
    {broken.map((b) => <div key={b.lane} className="agent-error">{b.label ?? b.lane}: {b.error}</div>)}
    <section className="panel agent-panel">
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr>
        <th>Lane</th><th>Since</th><th>Capital</th><th>Equity</th><th>Return</th><th>Realised</th><th>Unrealised</th><th>Open</th><th>Max DD</th>
        <th>Trades</th><th>Win</th><th>PF</th><th>Avg win / loss</th><th>Best week</th><th>Without best week</th><th>Book</th>
      </tr></thead><tbody>{books.map((b) => <tr key={b.lane} className={`openable-row${b.lane === book?.lane ? " active" : ""}`} onClick={() => setSelected(b.lane)} title={b.note ?? undefined}>
        <td><b>{b.label}</b>{b.note && <small> ⓘ</small>}</td><td>{day(b.since)}</td><td>{lakh(b.capital)}</td><td>{lakh(b.equity)}</td>
        <td className={tone(b.return_pct)}>{percent(b.return_pct)}</td><td className={tone(b.realized)}>{rupees(b.realized)}</td>
        <td className={tone(b.unrealized)}>{rupees(b.unrealized)}</td><td>{b.open_positions}</td>
        <td className={b.max_drawdown > 0 ? "negative" : ""}>{rupees(-b.max_drawdown)}<small> {percent(-b.max_drawdown_pct)}</small></td>
        <td>{b.closed.trades}</td><td>{win(b.closed.win_rate)}</td><td>{pf(b.closed.profit_factor)}</td>
        <td><span className="positive">{b.closed.avg_win == null ? "—" : rupees(b.closed.avg_win)}</span> / <span className="negative">{b.closed.avg_loss == null ? "—" : rupees(b.closed.avg_loss)}</span></td>
        <td className={tone(b.best_week?.net)}>{b.best_week ? <>{rupees(b.best_week.net)}<small> wk {day(b.best_week.week)}</small></> : "—"}</td>
        <td className={tone(b.net_without_best_week)}>{b.net_without_best_week == null ? "—" : rupees(b.net_without_best_week)}</td>
        <td className={b.book_full ? "negative" : ""}>{b.book_full == null ? "—" : b.book_full ? "Full" : "Open"}<small>{b.slots ? ` · ${b.open_positions}/${b.slots} slots` : b.ticket ? ` · cash ${lakh(b.cash)}` : ""}</small></td>
      </tr>)}</tbody></table></div>
    </section>

    <section className="panel agent-panel">
      <div className="panel-title">Return on capital over time <small>one % axis for every book · click a name to show or hide it · hover for the IST time</small></div>
      {books.length > 0 && <ReturnsChart books={returnBooks} height={300} initial={["macd", "smart_1l", "smart_50l", "spot_macd", "blast", "agentic"]} />}
    </section>

    <section className="panel agent-panel">
      <div className="panel-title">Weekly realised P&amp;L (IST week of exit)</div>
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Lane</th>{weeks.map((w) => <th key={w}>{day(w)}</th>)}</tr></thead>
        <tbody>{books.map((b) => { const by = Object.fromEntries(b.weekly.map((w) => [w.week, w.net]));
          return <tr key={b.lane}><td>{b.label}</td>{weeks.map((w) => <td key={w} className={tone(by[w])}>{by[w] == null ? "" : rupees(by[w])}</td>)}</tr>; })}</tbody></table></div>
    </section>

    {book && <section className="panel agent-panel">
      <div className="panel-title">{book.label} — exits by reason and hold time (net, trades)
        <select value={book.lane} onChange={(e) => setSelected(e.target.value)}>{books.map((b) => <option key={b.lane} value={b.lane}>{b.label}</option>)}</select></div>
      {reasons.length === 0 ? <div className="agent-empty">No exits yet.</div> :
        <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Exit reason</th>{holds.map((h) => <th key={h}>{h}</th>)}<th>All</th></tr></thead>
          <tbody>{reasons.map((r) => <tr key={r.reason}><td>{words(r.reason)}</td>
            {holds.map((h) => { const c = r.cells[h]; return <td key={h} className={tone(c?.net)} title={c ? `win ${win(c.win_rate)} · PF ${pf(c.profit_factor)}` : undefined}>{c ? <>{rupees(c.net)}<small> · {c.trades}</small></> : ""}</td>; })}
            <td className={tone(r.net)}>{rupees(r.net)}<small> · {r.trades}</small></td></tr>)}</tbody></table></div>}
    </section>}
  </div>;
}
