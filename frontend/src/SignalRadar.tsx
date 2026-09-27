import { useEffect, useMemo, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import type { Diagnostics, Signal, SignalEvaluation } from "./types";

const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const clock = (value: string | number) => new Date(typeof value === "number" ? value * 1000 : value).toLocaleTimeString("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false,
});
const contractName = (symbol: string) => symbol.split(":")[1] || symbol;

type View = "SIGNALS" | "CONDITIONS";
const VIEW_STORE = "macd.signalsTab";
// Safari's private mode throws on both halves of localStorage, and a rejected
// preference must not take the page down with it.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { return; } };

export function SignalRadar({ signals }: { signals: Signal[] }) {
  const [diag, setDiag] = useState<Diagnostics>();
  const [filter, setFilter] = useState<"NEAR" | "ALL">("NEAR");
  const [view, setView] = useState<View>(() => recall(VIEW_STORE) === "CONDITIONS" ? "CONDITIONS" : "SIGNALS");
  const pickView = (next: View) => { setView(next); remember(VIEW_STORE, next); };

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    let stopped = false;
    const load = () => {
      void fetch(`${API_URL}/api/signals/diagnostics`, { headers })
        .then((response) => response.ok ? response.json() : undefined)
        .then((data) => { if (!stopped && data) setDiag(data as Diagnostics); })
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 30_000);
    return () => { stopped = true; clearInterval(timer); };
  }, []);

  const rows = useMemo(() => {
    const all = diag?.rows || [];
    return filter === "NEAR" ? all.filter((row) => row.passed >= 1) : all;
  }, [diag, filter]);
  const totals = diag?.condition_totals;
  const enabled = diag?.enabled_conditions;
  const requiredCount = diag?.required_count ?? 1;

  return <section className="ledger-page signals-page">
    <div className="page-heading">
      <div><h1>Signals</h1><p>MACD crossing upward through zero is mandatory and includes gap-up jumps from below to above zero. KAMA, RSI(KAMA) and ROC(KAMA) are shown for context unless enabled as confirmations in Settings.</p></div>
      <span>{signals.length} fired · {diag?.evaluated ?? 0} evaluated</span>
    </div>
    <div className="radar-chips">
      <RadarChip label="Contracts evaluated" value={diag?.evaluated} />
      <RadarChip label="MACD cross now" value={totals?.cross} />
      <RadarChip label="KAMA rising" value={totals?.kama_ok} />
      <RadarChip label="RSI / ROC pass" value={(totals?.rsi_ok ?? 0) + (totals?.roc_ok ?? 0)} />
      <RadarChip label={`${requiredCount} active → signal`} value={totals?.fired} highlight />
    </div>
    {/* Two full-height tabs, not two half panes: the radar carries eleven
        columns and scrolled inside two thirds of the page while the fired-signal
        list above it was usually empty. */}
    <div className="watch-tabs book-tabs signals-tabs">
      <button className={view === "SIGNALS" ? "active" : ""} onClick={() => pickView("SIGNALS")}>Signals<span>{signals.length}</span></button>
      <button className={view === "CONDITIONS" ? "active" : ""} onClick={() => pickView("CONDITIONS")}>Conditions<span>{diag?.evaluated ?? 0}</span></button>
    </div>
    <div className="full-ledger panel signals-panel">
      {view === "SIGNALS" && <section className="book-section">
        <div className="book-heading"><b>Fired signals</b><span>{signals.length}</span></div>
        <div className="book-body ledger-table-wrap">
          <table className="ledger-table"><thead><tr><th className="plain">Time (IST)</th><th className="plain">Symbol</th><th className="plain">Side</th><th className="plain">Trigger</th><th className="plain">Premium</th><th className="plain">MACD</th></tr></thead>
            <tbody>{signals.length ? signals.map((row) => <tr key={row.signal_id}>
              <td>{clock(row.timestamp as unknown as string)}</td>
              <td>{contractName(row.symbol)}</td>
              <td className={row.side === "BUY" ? "positive" : "negative"}>{row.side}</td>
              <td>{row.kind}</td>
              <td>₹{number.format(row.price)}</td>
              <td>{number.format(row.macd)}</td>
            </tr>) : <tr><td colSpan={6} className="empty">No signals fired yet this session</td></tr>}</tbody>
          </table>
        </div>
      </section>}
      {view === "CONDITIONS" && <section className="book-section">
        <div className="book-heading"><b>Entry-condition radar</b>
          <div className="radar-filter">{(["NEAR", "ALL"] as const).map((key) => <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{key === "NEAR" ? "Near misses (1+)" : `All ${diag?.evaluated ?? 0}`}</button>)}</div>
        </div>
        <div className="book-body ledger-table-wrap">
          <table className="ledger-table"><thead><tr><th className="plain">Contract</th><th className="plain">Last close (IST)</th><th className="plain">Premium</th><th className="plain">MACD prev → now</th><th className="plain">Cross</th><th className="plain">KAMA{enabled?.kama ? "" : " (info)"}</th><th className="plain">RSI(KAMA){enabled?.kama_rsi ? "" : " (info)"}</th><th className="plain">ROC(KAMA){enabled?.kama_roc ? "" : " (info)"}</th><th className="plain">BB (info)</th><th className="plain">Vol ratio (info)</th><th className="plain">Passed</th></tr></thead>
            <tbody>{rows.length ? rows.map((row) => <tr key={row.symbol} className={row.fired ? "radar-fired" : ""}>
              <td>{contractName(row.symbol)}</td>
              <td>{clock(row.timestamp)}</td>
              <td>₹{number.format(row.close)}</td>
              <td className={row.macd >= 0 ? "positive" : "negative"}>{number.format(row.previous_macd)} → {number.format(row.macd)}</td>
              <Cond ok={row.cross} /><Cond ok={row.kama_ok} info={!row.kama_required} />
              <td className={`${row.rsi_ok ? "cond ok" : "cond"}${row.rsi_required ? "" : " info"}`}>{row.kama_rsi === undefined || row.kama_rsi === null ? "—" : number.format(row.kama_rsi)}</td>
              <td className={`${row.roc_ok ? "cond ok" : "cond"}${row.roc_required ? "" : " info"}`}>{row.kama_roc === undefined || row.kama_roc === null ? "—" : `${number.format(row.kama_roc)}%`}</td>
              <td className="cond info">{row.bb_ok ? "in" : "out"}</td>
              <td className="cond info">{row.volume_ratio.toFixed(2)}×</td>
              <td><span className={`passed-pill p${row.fired ? 4 : row.passed >= 2 ? 3 : row.passed}`}>{row.passed}/{row.required_count ?? requiredCount}</span></td>
            </tr>) : <tr><td colSpan={11} className="empty">{diag ? "No contracts near the entry conditions right now" : "Waiting for the first closed candles…"}</td></tr>}</tbody>
          </table>
        </div>
      </section>}
    </div>
  </section>;
}

function RadarChip({ label, value, highlight }: { label: string; value?: number; highlight?: boolean }) {
  return <div className={highlight ? "radar-chip highlight" : "radar-chip"}><span>{label}</span><b>{value ?? "—"}</b></div>;
}

function Cond({ ok, info }: { ok: boolean; info?: boolean }) {
  return <td className={`${ok ? "cond ok" : "cond"}${info ? " info" : ""}`}>{ok ? "✓" : "·"}</td>;
}
