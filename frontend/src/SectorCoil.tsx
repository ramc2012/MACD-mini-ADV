import { useEffect, useMemo, useRef, useState } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";
import { arrowHead, axis, coilPath, filled, rowHeight, story, type CoilRow } from "./sectorCoilMath";

// Sector coil, after Barron's "The Big Reversal" (owner, 7 Nov 2026). Every index starts at 0 % on the base date; its
// line runs to its return at each checkpoint, dropping one row per checkpoint, with a U-turn where the direction flips
// and a small loop where it keeps going. Blue above zero, red below. Data: /api/sector-coil (daily closes shared with
// the Sector wheel; live price for today during the session).
type Payload = { as_of: string; live: boolean; base: string; base_day: string | null; step: string; measure: string;
  points: string[]; rows: CoilRow[]; missing: string[]; refreshing?: boolean };
type Prefs = { base: string; step: string; measure: string; sort: string };

const BASES = [["YTD", "Since 31 Dec"], ["1Y", "1 year"], ["6M", "6 months"], ["3M", "3 months"], ["1M", "1 month"]] as const;
const STEPS = [["M", "Monthly"], ["2W", "Fortnightly"], ["W", "Weekly"], ["TEMPLATE", "One month ago"]] as const;
const MEASURES = [["own", "Own return"], ["relative", "vs NIFTY 50"]] as const;
const SORTS = [["NOW", "Now"], ["PREV", "Previous turn"], ["PEAK", "Peak"], ["STEP", "Last step"]] as const;
const UP = "#4d91ff", DOWN = "#f15b6c";
const W = 960, LEFT = 16, PLOT_RIGHT = W - 150, TOP = 64, GAP = 18, BENCH_GAP = 26;
const PREFS_KEY = "macd.sectorCoil";

const fmtDay = (d: string) => new Date(`${d}T00:00:00+05:30`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
const fmtDayYear = (d: string) => new Date(`${d}T00:00:00+05:30`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);

function loadPrefs(): Prefs {
  const fallback = { base: "YTD", step: "M", measure: "own", sort: "NOW" };
  try { return { ...fallback, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; } catch { return fallback; }
}

export function SectorCoil() {
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState<{ symbol: string; x: number; y: number } | null>(null);
  const [table, setTable] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const set = (patch: Partial<Prefs>) => setPrefs((p) => {
    const next = { ...p, ...patch };
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* private window: keep in memory */ }
    return next;
  });

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const controller = new AbortController();
    const query = new URLSearchParams({ base: prefs.base, step: prefs.step, measure: prefs.measure });
    const load = () => void requestJson<Payload>(`${API_URL}/api/sector-coil?${query}`, { headers, signal: controller.signal })
      .then((payload) => { setData(payload); setError(""); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unavailable"); });
    load();
    const timer = setInterval(load, 60_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [prefs.base, prefs.step, prefs.measure]);

  const dates = useMemo(() => (data?.base_day ? [data.base_day, ...data.points] : []), [data]);
  const relative = data?.measure === "relative";
  const bench = data?.rows.find((r) => r.kind === "benchmark");
  const rows = useMemo(() => {
    const list = (data?.rows || []).filter((r) => r.kind !== "benchmark").map((r) => ({ row: r, v: filled(r.values) }));
    const n = dates.length - 1;
    const key = (v: number[]) => prefs.sort === "PREV" ? v[Math.max(0, n - 1)] : prefs.sort === "PEAK" ? Math.max(...v) : prefs.sort === "STEP" ? v[n] - v[Math.max(0, n - 1)] : v[n];
    return list.sort((a, b) => key(b.v) - key(a.v));
  }, [data, dates, prefs.sort]);

  const segments = Math.max(1, dates.length - 1);
  const rowH = rowHeight(segments);
  const blockH = (segments - 1) * rowH;
  const showBench = !!bench && !relative;
  const all = [...rows.flatMap((r) => r.v), ...(showBench && bench ? filled(bench.values) : [])];
  const ax = axis(all.length ? all : [0, 1]);
  const x = (v: number) => LEFT + ((v - ax.lo) / (ax.hi - ax.lo)) * (PLOT_RIGHT - LEFT);
  const x0 = x(0);
  const benchTop = TOP + 6;
  const sectorsTop = showBench ? benchTop + blockH + BENCH_GAP + 10 : TOP + 6;
  const height = sectorsTop + rows.length * (blockH + GAP) + 26;
  const tale = useMemo(() => story(data?.rows || [], dates, fmtDay, relative ? " pts" : "%"), [data, dates, relative]);
  const focus = hover && (data?.rows || []).find((r) => r.symbol === hover.symbol);

  const block = (row: CoilRow, v: number[], top: number, key: string, annotate = false) => {
    const xs = v.map(x);
    const dim = hover && hover.symbol !== row.symbol;
    const last = v[v.length - 1];
    const maxX = Math.max(...xs) + rowH / 2 + 8, minX = Math.min(...xs) - rowH / 2 - 8;
    const name = `${row.label}`;
    const labelRight = maxX + name.length * 6.4 + 46 < W;
    const d = coilPath(xs, top, rowH);
    return <g key={key} opacity={dim ? 0.28 : 1}
      onMouseMove={(e) => { const box = wrap.current?.getBoundingClientRect(); if (box) setHover({ symbol: row.symbol, x: e.clientX - box.left, y: e.clientY - box.top }); }}
      onMouseLeave={() => setHover(null)}>
      <rect x={Math.min(minX, x0) - 4} y={top - rowH / 2 - 6} width={Math.max(maxX, x0) - Math.min(minX, x0) + 130} height={blockH + rowH + 12} fill="transparent" />
      <path d={d} fill="none" stroke={UP} strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" clipPath="url(#coil-up)" />
      <path d={d} fill="none" stroke={DOWN} strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" clipPath="url(#coil-down)" />
      <path d={arrowHead(xs, top, rowH)} fill={last >= 0 ? UP : DOWN} />
      <text x={labelRight ? maxX : minX} y={top + 0.5} className="coil-label" textAnchor={labelRight ? "start" : "end"} dominantBaseline="middle">
        {name} <tspan className="coil-value">{pct(last)}</tspan></text>
      {annotate && <>
        <text x={xs[xs.length - 1] + (xs[xs.length - 1] >= xs[xs.length - 2] ? 10 : -10)} y={top + blockH + 1} className="coil-note coil-note-now"
          textAnchor={xs[xs.length - 1] >= xs[xs.length - 2] ? "start" : "end"} dominantBaseline="middle">{fmtDay(dates[dates.length - 1])}{data?.live ? " (live)" : ""}</text>
      </>}
    </g>;
  };

  return <section className="panel sector-coil">
    <div className="heatmap-toolbar">
      <div className="risk-tabs" role="group" aria-label="Base">{BASES.map(([key, label]) =>
        <button key={key} aria-pressed={prefs.base === key} onClick={() => set({ base: key })}>{label}</button>)}</div>
      <div className="risk-tabs" role="group" aria-label="Turns">{STEPS.map(([key, label]) =>
        <button key={key} aria-pressed={prefs.step === key} onClick={() => set({ step: key })}>{label}</button>)}</div>
      <div className="risk-tabs" role="group" aria-label="Measure">{MEASURES.map(([key, label]) =>
        <button key={key} aria-pressed={prefs.measure === key} onClick={() => set({ measure: key })}>{label}</button>)}</div>
      <label className="coil-sort">Sort <select value={prefs.sort} onChange={(e) => set({ sort: e.target.value })}>
        {SORTS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <button className="coil-table-toggle" aria-pressed={table} onClick={() => setTable((t) => !t)}>{table ? "Chart" : "Table"}</button>
    </div>
    <div className="heatmap-meta">{data
      ? <>Daily closes {data.base_day ? fmtDayYear(data.base_day) : "—"} → {fmtDayYear(data.as_of)}{data.live ? " (live price for today)" : ""} · {dates.length - 1} turns · one row per checkpoint: a U-turn where the move reversed, a loop where it carried on{data.refreshing ? " · refreshing daily closes…" : ""}</>
      : error || "Loading…"}</div>
    {!!data?.missing?.length && <div role="status" className="coil-missing">No close on the base date for {data.missing.map((s) => s.split(":")[1].replace("-INDEX", "")).join(", ")}; left out.</div>}
    {data && !data.rows.length && <div className="chart-empty">No daily closes stored for this span yet{data.refreshing ? " — the refresh is running" : ""}.</div>}
    {data && data.rows.length > 0 && !table && <div className="coil-wrap" ref={wrap}>
      <h3 className="coil-title">{tale.title}</h3>
      <p className="coil-story">{tale.text}</p>
      <svg viewBox={`0 0 ${W} ${height}`} className="coil-svg" role="img" aria-label={`Sector coil: ${tale.title}. ${tale.text}`}>
        <defs>
          <clipPath id="coil-up"><rect x={x0} y={0} width={W - x0} height={height} /></clipPath>
          <clipPath id="coil-down"><rect x={0} y={0} width={x0} height={height} /></clipPath>
        </defs>
        <text x={x0 - 8} y={TOP - 30} className="coil-dir" fill={DOWN} textAnchor="end">← Down</text>
        <text x={x0 + 8} y={TOP - 30} className="coil-dir" fill={UP}>{relative ? "Ahead of NIFTY 50" : prefs.base === "YTD" ? "Up year to date" : "Up since base"} →</text>
        {dates.length > 0 && <text x={x0} y={TOP - 12} className="coil-note" textAnchor="middle">0 = close of {fmtDayYear(dates[0])}</text>}
        {ax.ticks.map((t) => <line key={t} x1={x(t)} x2={x(t)} y1={TOP - 4} y2={height - 22} className={t === 0 ? "coil-zero" : "coil-grid"} />)}
        {ax.ticks.map((t, i) => <text key={`l${t}`} x={x(t)} y={height - 6} className="coil-tick" textAnchor="middle">{t}{i === ax.ticks.length - 1 ? (relative ? " pts" : "%") : ""}</text>)}
        {showBench && bench && <>
          {block(bench, filled(bench.values), benchTop, "bench", true)}
          <line x1={LEFT} x2={W - 8} y1={benchTop + blockH + BENCH_GAP / 2 + 6} y2={benchTop + blockH + BENCH_GAP / 2 + 6} className="coil-sep" />
        </>}
        {rows.map(({ row, v }, i) => block(row, v, sectorsTop + i * (blockH + GAP), row.symbol, !showBench && i === 0))}
      </svg>
      {focus && hover && <div className="coil-tip" style={{ left: Math.min(hover.x + 14, (wrap.current?.clientWidth || 600) - 230), top: hover.y + 12 }}>
        <b>{focus.label}</b>{relative ? " vs NIFTY 50" : ""}
        <table><tbody>{dates.map((d, i) => {
          const v = filled(focus.values)[i];
          const prev = i ? filled(focus.values)[i - 1] : null;
          return <tr key={d}><td>{fmtDayYear(d)}</td><td style={{ color: v >= 0 ? UP : DOWN }}>{pct(v)}</td>
            <td className="coil-step">{prev === null ? "base" : `${v - prev >= 0 ? "▲" : "▼"} ${Math.abs(v - prev).toFixed(1)}`}</td></tr>;
        })}</tbody></table>
      </div>}
    </div>}
    {data && data.rows.length > 0 && table && <div className="coil-table-wrap"><table className="ledger-table coil-table">
      <thead><tr><th>Index</th>{dates.map((d, i) => <th key={d}>{i === 0 ? `Base ${fmtDay(d)}` : fmtDay(d)}</th>)}<th>Last step</th></tr></thead>
      <tbody>{[...(bench && !relative ? [{ row: bench, v: filled(bench.values) }] : []), ...rows].map(({ row, v }) =>
        <tr key={row.symbol}><td><b>{row.label}</b></td>{v.map((val, i) => <td key={i} style={{ color: val >= 0 ? UP : DOWN }}>{pct(val)}</td>)}
          <td>{v.length > 1 ? pct(v[v.length - 1] - v[v.length - 2]).replace("%", " pts") : "—"}</td></tr>)}</tbody>
    </table></div>}
  </section>;
}
