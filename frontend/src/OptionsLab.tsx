import { useEffect, useMemo, useRef, useState } from "react";
import { rupees, tone } from "./agentMath";
import {
  breakevens, expiryPnl, extremes, impliedVol, netPremium, roughMargin, spotGrid, strategyGreeks, strategyPnl, TEMPLATES, yearsTo,
  type Leg,
} from "./payoffMath";
import { useCachedJson } from "./useCachedJson";
import "./optionslab.css";

// Options lab (owner, 18 Nov 2026): IV scanner, strategy builder (payoff + what-if) and the stock-option expiry /
// rollover view. Everything here is read-only analysis on the engine's stored chains; nothing places an order.

export const LAB_VIEWS = [["IV", "IV scanner"], ["BUILDER", "Strategy builder"], ["EXPIRY", "Expiry & rollover"]] as const;
export type LabView = typeof LAB_VIEWS[number][0];

type ScanRow = {
  underlying: string; kind: "index" | "stock"; sector: string; spot: number; strike: number; expiry: string; dte: number;
  iv: number; tenor: string; expiry_iv: number | null; ce_iv: number | null; pe_iv: number | null; skew: number | null;
  straddle_pct: number; iv_rank: number | null; iv_pctl: number | null; sessions: number; iv_low: number; iv_high: number;
  iv_chg_1d: number | null; iv_chg_5d: number | null; hv20: number | null; iv_hv: number | null; as_of: string; series: number[];
};
type Scan = { as_of: string; tenor_days: number; rows: ScanRow[]; source: string };
type ChainLeg = { symbol: string; ltp: number | null; bid: number | null; ask: number | null; oi: number | null; volume: number | null; iv: number | null };
type ChainRow = { strike: number; ce?: ChainLeg; pe?: ChainLeg };
type Stage = { stage: string; day: string; delivery_margin_pct: number; note?: string };
type Chain = {
  underlying: string; kind: "index" | "stock"; as_of: string; spot: number | null; futures: number | null; expiry: string; expiries: string[];
  days_to_expiry: number; rows: ChainRow[]; delivery: Stage[]; lot_size: number | null; spot_symbol: string;
};
type Held = { lane: string; symbol: string; kind: "CE" | "PE"; strike: number; expiry: string; quantity: number; average_price: number; last_price: number; lot_size: number };
type ExposureRow = {
  lane: string; symbol: string; kind: string; quantity: number; average_price: number; last_price: number | null; strike: number | null;
  spot: number | null; expiry: string; sessions_to_expiry: number | null; last_day: string | null; itm: boolean; liable: boolean;
  stage_pct: number; contract_value: number | null; margin_today: number | null; margin_e1: number;
};
type Exposure = {
  as_of: string; delivery_rate: number; stock_option_exit_sessions: number; rows: ExposureRow[]; schedules: Record<string, Stage[]>;
  lanes: { lane: string; positions: number; liable: number; margin_today: number; margin_e1: number }[];
};

const fixed = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
const signed = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
const dayLabel = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const istNow = () => new Date(Date.now() + 330 * 60_000).toISOString().slice(0, 10);

export function OptionsLabPage({ view, onView }: { view: LabView; onView: (view: LabView) => void }) {
  const [underlying, setUnderlying] = useState("NIFTY");
  return <div className="agent-page lanes-page options-lab">
    <div className="agent-head panel-row">
      <div><b>Options lab</b><span className="agent-sub">IV scanner · strategy builder with payoff and what-if · stock-option expiry margin · analysis only, no orders</span></div>
    </div>
    <nav className="sub-tabs" aria-label="Options lab views">{LAB_VIEWS.map(([key, label]) => <button key={key} className={view === key ? "active" : ""} aria-current={view === key ? "page" : undefined} onClick={() => onView(key)}>{label}</button>)}</nav>
    {view === "IV" ? <IvScanner onOpen={(name) => { setUnderlying(name); onView("BUILDER"); }} />
      : view === "BUILDER" ? <StrategyBuilder underlying={underlying} onUnderlying={setUnderlying} />
      : <ExpiryView onOpen={(name) => { setUnderlying(name); onView("BUILDER"); }} />}
  </div>;
}

// ------------------------------------------------------------------------------------------------ IV scanner

type ScanKey = "underlying" | "iv" | "iv_rank" | "iv_pctl" | "iv_chg_1d" | "iv_chg_5d" | "hv20" | "iv_hv" | "skew" | "straddle_pct";
const PRESETS = [["ALL", "All"], ["HIGH", "IV rank ≥ 70"], ["LOW", "IV rank ≤ 30"], ["RICH", "IV ≥ 1.2× HV"], ["CHEAP", "IV ≤ 0.85× HV"], ["JUMP", "IV up ≥ 3 pts today"]] as const;

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const points = values.map((v, i) => `${((i / (values.length - 1)) * 58 + 1).toFixed(1)},${(15 - ((v - lo) / span) * 13).toFixed(1)}`).join(" ");
  return <svg className="lab-spark" viewBox="0 0 60 16" width="60" height="16" aria-hidden="true"><polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /></svg>;
}

function IvScanner({ onOpen }: { onOpen: (underlying: string) => void }) {
  const { data, error, loading, reload } = useCachedJson<Scan>("/api/iv-scanner", 120_000);
  const [kind, setKind] = useState<"all" | "index" | "stock">("all");
  const [preset, setPreset] = useState<typeof PRESETS[number][0]>("ALL");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: ScanKey; desc: boolean }>({ key: "iv_rank", desc: true });
  const rows = useMemo(() => {
    const keep = (r: ScanRow) => (kind === "all" || r.kind === kind)
      && (!search || r.underlying.includes(search) || r.sector.toUpperCase().includes(search))
      && (preset === "ALL" || (preset === "HIGH" && (r.iv_rank ?? -1) >= 70) || (preset === "LOW" && r.iv_rank != null && r.iv_rank <= 30)
        || (preset === "RICH" && (r.iv_hv ?? 0) >= 1.2) || (preset === "CHEAP" && r.iv_hv != null && r.iv_hv <= 0.85) || (preset === "JUMP" && (r.iv_chg_1d ?? 0) >= 3));
    const value = (r: ScanRow) => sort.key === "underlying" ? r.underlying : r[sort.key];
    return (data?.rows ?? []).filter(keep).sort((a, b) => {
      const x = value(a), y = value(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      const order = typeof x === "string" ? x.localeCompare(String(y)) : (x as number) - (y as number);
      return sort.desc ? -order : order;
    });
  }, [data, kind, preset, search, sort]);
  const head = (key: ScanKey, label: string, title?: string) => <th title={title} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : "none"}>
    <button className="lab-sort" onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "underlying" }))}>{label}{sort.key === key ? (sort.desc ? " ▼" : " ▲") : ""}</button></th>;
  if (error && !data) return <div className="agent-error" role="alert">{error}</div>;
  if (!data) return <div className="agent-empty state-loading" role="status">{loading ? "Reading the stored chains…" : "Loading…"}</div>;
  return <section className="panel agent-panel">
    <div className="panel-title">IV scanner <small>{rows.length} of {data.rows.length} underlyings · {data.tenor_days}-day ATM IV · as of {data.as_of.slice(11, 16)} IST</small>
      <button className="settings-button" onClick={reload}>Refresh</button></div>
    <div className="lab-filters">
      <div className="agent-tabs" role="group" aria-label="Kind">{(["all", "index", "stock"] as const).map((k) => <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{k === "all" ? "All" : k === "index" ? "Indices" : "Stocks"}</button>)}</div>
      <div className="agent-tabs" role="group" aria-label="Filter">{PRESETS.map(([key, label]) => <button key={key} className={preset === key ? "active" : ""} onClick={() => setPreset(key)}>{label}</button>)}</div>
      <label>Search <input type="search" value={search} placeholder="name or sector" onChange={(e) => setSearch(e.target.value.toUpperCase())} /></label>
    </div>
    <p className="agent-sub bo-note">IV = {data.tenor_days}-day constant-maturity at-the-money IV from the stored chains (stocks: interpolated between the two stored months; index chains keep the front weekly, so their tenor is shown). IV rank = today against this name's own low–high over the sessions stored (stocks since 26 Oct, indices since 3 Sep), so <b>ranks are rough until the history is longer</b>. HV20 = 20-session realised volatility of the spot. Skew = put IV − call IV at the ATM strike. Straddle = ATM call + put as % of spot, to the shown expiry (the market's expected move). Click a row to open it in the strategy builder.</p>
    <div className="live-table-wrap"><table className="live-table agent-table lab-table"><thead><tr>
      {head("underlying", "Underlying")}<th>Spot</th>{head("iv", "IV")}<th>Tenor</th>{head("iv_rank", "IV rank", "0 = lowest, 100 = highest IV in the stored history")}
      {head("iv_pctl", "IV %ile", "share of earlier sessions with lower IV")}<th>Range</th><th>Trend</th>{head("iv_chg_1d", "Δ 1d")}{head("iv_chg_5d", "Δ 5d")}
      {head("hv20", "HV20")}{head("iv_hv", "IV/HV")}{head("skew", "Skew")}{head("straddle_pct", "Straddle")}<th>Expiry</th><th>Sessions</th>
    </tr></thead><tbody>{rows.map((r) => <tr key={r.underlying} className="lab-click" tabIndex={0} onClick={() => onOpen(r.underlying)} onKeyDown={(e) => { if (e.key === "Enter") onOpen(r.underlying); }}>
      <td><b>{r.underlying}</b><small> {r.sector}</small></td><td>{fixed(r.spot, 2)}</td><td><b>{fixed(r.iv)}</b></td><td>{r.tenor}</td>
      <td className={`lab-rank lab-rank-${r.iv_rank == null ? "na" : r.iv_rank >= 70 ? "hi" : r.iv_rank <= 30 ? "lo" : "mid"}`}>{fixed(r.iv_rank, 0)}</td>
      <td>{fixed(r.iv_pctl, 0)}</td><td><small>{fixed(r.iv_low)}–{fixed(r.iv_high)}</small></td><td><Spark values={r.series} /></td>
      <td className={tone(r.iv_chg_1d)}>{signed(r.iv_chg_1d)}</td><td className={tone(r.iv_chg_5d)}>{signed(r.iv_chg_5d)}</td>
      <td>{fixed(r.hv20)}</td><td className={r.iv_hv == null ? "" : r.iv_hv >= 1.2 ? "negative" : r.iv_hv <= 0.85 ? "positive" : ""}>{fixed(r.iv_hv, 2)}</td>
      <td>{signed(r.skew)}</td><td>{fixed(r.straddle_pct, 2)}%</td><td>{dayLabel(r.expiry)} <small>{r.dte}d</small></td><td>{r.sessions}</td>
    </tr>)}</tbody></table></div>
  </section>;
}

// ------------------------------------------------------------------------------------------------ strategy builder

let legSeq = 0;
const newId = () => `L${++legSeq}`;
const mid = (leg?: ChainLeg) => !leg ? null : leg.bid && leg.ask && leg.ask >= leg.bid && leg.bid > 0 ? (leg.bid + leg.ask) / 2 : leg.ltp;

function StrategyBuilder({ underlying, onUnderlying }: { underlying: string; onUnderlying: (u: string) => void }) {
  const names = useCachedJson<{ rows: { underlying: string; kind: string; day: string }[] }>("/api/builder/underlyings");
  const [expiry, setExpiry] = useState<string>("");
  const chainPath = `/api/builder/chain?underlying=${encodeURIComponent(underlying)}${expiry ? `&expiry=${expiry}` : ""}`;
  const chain = useCachedJson<Chain>(chainPath, 60_000);
  const held = useCachedJson<{ rows: Held[] }>(`/api/builder/positions?underlying=${encodeURIComponent(underlying)}`, 60_000);
  const [legs, setLegs] = useState<Leg[]>([]);
  const [lotOverride, setLotOverride] = useState<number | null>(null);
  const [spotShift, setSpotShift] = useState(0);
  const [daysForward, setDaysForward] = useState(0);
  const [ivShift, setIvShift] = useState(0);
  const [shortPct, setShortPct] = useState(0);
  const lastUnderlying = useRef(underlying);
  useEffect(() => {
    if (lastUnderlying.current !== underlying) { lastUnderlying.current = underlying; setLegs([]); setExpiry(""); setLotOverride(null); setSpotShift(0); setDaysForward(0); setIvShift(0); }
  }, [underlying]);
  const c = chain.data && chain.data.underlying === underlying ? chain.data : undefined;
  const lot = lotOverride ?? c?.lot_size ?? 1;
  const spot = c?.spot ?? 0;
  const margin = shortPct || (c?.kind === "index" ? 12 : 20);
  const strikes = useMemo(() => (c?.rows ?? []).map((r) => r.strike), [c]);
  const atm = useMemo(() => strikes.length ? strikes.reduce((a, b) => Math.abs(b - spot) < Math.abs(a - spot) ? b : a) : 0, [strikes, spot]);
  const step = useMemo(() => {
    const diffs = strikes.slice(1).map((k, i) => k - strikes[i]).filter((d) => d > 0);
    return diffs.length ? Math.min(...diffs) : 1;
  }, [strikes]);
  const atmIv = useMemo(() => {
    const row = c?.rows.find((r) => r.strike === atm);
    const ivs = [row?.ce?.iv, row?.pe?.iv].filter((v): v is number => v != null);
    return ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : 15;
  }, [c, atm]);

  const addLeg = (kind: "CE" | "PE", side: "BUY" | "SELL", strike: number) => {
    if (!c) return;
    const row = c.rows.find((r) => r.strike === strike);
    const quote = kind === "CE" ? row?.ce : row?.pe;
    const price = side === "BUY" ? (quote?.ask || mid(quote) || 0) : (quote?.bid || mid(quote) || 0);
    setLegs((current) => [...current, { id: newId(), kind, side, strike, lots: 1, lotSize: lot, price: Number(price.toFixed(2)), iv: quote?.iv ?? atmIv, expiry: c.expiry, symbol: quote?.symbol }]);
  };
  const applyTemplate = (key: string) => {
    if (!c) return;
    setLegs([]);
    const nearest = (target: number) => strikes.reduce((a, b) => Math.abs(b - target) < Math.abs(a - target) ? b : a, atm);
    TEMPLATES[key].legs.forEach((t) => addLeg(t.kind, t.side, nearest(atm + t.offset * step)));
  };
  const loadHeld = (lane: string) => {
    const rows = (held.data?.rows ?? []).filter((r) => r.lane === lane);
    const now = new Date();
    setLegs(rows.map((r) => {
      const years = yearsTo(r.expiry, now);
      const iv = (r.last_price && spot ? impliedVol(r.last_price, spot, r.strike, years, r.kind === "CE") : null) ?? atmIv;
      const size = r.lot_size || lot;
      return { id: newId(), kind: r.kind, side: r.quantity > 0 ? "BUY" : "SELL", strike: r.strike, lots: Math.abs(r.quantity) / size, lotSize: size,
        price: Number(r.average_price.toFixed(2)), iv: Number(iv.toFixed(2)), expiry: r.expiry, symbol: r.symbol };
    }));
  };
  const update = (id: string, patch: Partial<Leg>) => setLegs((current) => current.map((leg) => leg.id === id ? { ...leg, ...patch } : leg));
  const lanesHolding = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of held.data?.rows ?? []) out[r.lane] = (out[r.lane] ?? 0) + 1;
    return Object.entries(out);
  }, [held.data]);

  const now = useMemo(() => new Date(), [c?.as_of, legs.length]);  // eslint-disable-line react-hooks/exhaustive-deps
  const firstExpiry = legs.length ? legs.map((l) => l.expiry).sort()[0] : c?.expiry ?? istNow();
  const maxDays = Math.max(0, Math.ceil(yearsTo(firstExpiry, now) * 365));
  const at = new Date(Math.min(now.getTime() + daysForward * 86_400_000, Date.parse(`${firstExpiry}T15:30:00+05:30`)));
  const whatSpot = spot * (1 + spotShift / 100);
  const straddle = (() => { const row = c?.rows.find((r) => r.strike === atm); const v = (mid(row?.ce) ?? 0) + (mid(row?.pe) ?? 0); return spot ? (v / spot) * 100 : 0; })();
  const width = Math.min(30, Math.max(6, straddle * 3, Math.abs(spotShift) + 3));
  const grid = useMemo(() => spot ? spotGrid(spot, width, 121, legs.map((l) => l.strike)) : [], [spot, width, legs]);
  const curve = useMemo(() => grid.map((s) => ({ spot: s, expiry: expiryPnl(legs, s), at: strategyPnl(legs, s, at, ivShift) })), [grid, legs, at.getTime(), ivShift]);  // eslint-disable-line react-hooks/exhaustive-deps
  const bes = useMemo(() => breakevens(curve.map((p) => ({ spot: p.spot, pnl: p.expiry }))), [curve]);
  const ext = useMemo(() => spot && legs.length ? extremes(legs, spot) : { maxProfit: null, maxLoss: null }, [legs, spot]);
  const greeks = useMemo(() => strategyGreeks(legs, whatSpot, at, ivShift), [legs, whatSpot, at.getTime(), ivShift]);  // eslint-disable-line react-hooks/exhaustive-deps
  const pnlNow = legs.length ? strategyPnl(legs, whatSpot, at, ivShift) : 0;
  const premium = netPremium(legs);
  const marginNeed = legs.length ? roughMargin(legs, spot, margin) : 0;
  const stockItm = c?.kind === "stock" && legs.some((l) => (l.kind === "CE" ? spot > l.strike : spot < l.strike) || l.side === "SELL");
  const moves = [-5, -3, -2, -1, 0, 1, 2, 3, 5];
  const midDate = new Date(now.getTime() + (Date.parse(`${firstExpiry}T15:30:00+05:30`) - now.getTime()) / 2);

  return <>
    <section className="panel agent-panel lab-controls">
      <label>Underlying <select value={underlying} onChange={(e) => onUnderlying(e.target.value)}>
        {(names.data?.rows ?? [{ underlying, kind: "index", day: "" }]).map((r) => <option key={r.underlying} value={r.underlying}>{r.underlying}</option>)}</select></label>
      <label>Expiry <select value={c?.expiry ?? ""} onChange={(e) => setExpiry(e.target.value)}>{(c?.expiries ?? []).map((e) => <option key={e} value={e}>{dayLabel(e)}</option>)}</select></label>
      <label>Lot size <input type="number" min={1} value={lot} onChange={(e) => setLotOverride(Math.max(1, Number(e.target.value) || 1))} /></label>
      <span className="agent-sub">{c ? <>spot <b>{fixed(c.spot, 2)}</b>{c.futures ? <> · fut {fixed(c.futures, 2)}</> : null} · ATM IV {fixed(atmIv)} · chain {c.as_of.slice(11, 16)} IST</> : chain.error || "Loading the chain…"}</span>
    </section>
    <section className="panel agent-panel">
      <div className="panel-title">Build <small>pick a ready-made strategy, add legs from the chain, or load a lane's open positions on {underlying}</small></div>
      <div className="lab-templates">
        {Object.entries(TEMPLATES).map(([key, t]) => <button key={key} className="settings-button" disabled={!c} onClick={() => applyTemplate(key)}>{t.label}</button>)}
        {lanesHolding.map(([lane, n]) => <button key={lane} className="settings-button lab-held" onClick={() => loadHeld(lane)}>Load {lane} ({n})</button>)}
        {legs.length > 0 && <button className="settings-button" onClick={() => setLegs([])}>Clear</button>}
      </div>
      {legs.length > 0 && <div className="live-table-wrap"><table className="live-table agent-table lab-legs"><thead><tr><th>Side</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Lots</th><th>Entry</th><th>IV %</th><th>Now (model)</th><th>P&amp;L at what-if</th><th /></tr></thead>
        <tbody>{legs.map((leg) => {
          const value = leg.kind === "FUT" ? whatSpot : strategyPnl([{ ...leg, side: "BUY", lots: 1, lotSize: 1, price: 0 }], whatSpot, at, ivShift);
          return <tr key={leg.id}>
            <td><button className={`lab-side ${leg.side === "BUY" ? "positive" : "negative"}`} onClick={() => update(leg.id, { side: leg.side === "BUY" ? "SELL" : "BUY" })}>{leg.side}</button></td>
            <td>{leg.kind}</td>
            <td><select value={leg.strike} onChange={(e) => update(leg.id, { strike: Number(e.target.value) })}>{[...new Set([...strikes, leg.strike])].sort((a, b) => a - b).map((k) => <option key={k} value={k}>{k}</option>)}</select></td>
            <td>{dayLabel(leg.expiry)}</td>
            <td><input type="number" min={1} step={1} value={leg.lots} onChange={(e) => update(leg.id, { lots: Math.max(1, Number(e.target.value) || 1) })} /></td>
            <td><input type="number" min={0} step={0.05} value={leg.price} onChange={(e) => update(leg.id, { price: Math.max(0, Number(e.target.value) || 0) })} /></td>
            <td><input type="number" min={1} step={0.5} value={leg.iv} onChange={(e) => update(leg.id, { iv: Math.max(1, Number(e.target.value) || 1) })} /></td>
            <td>{fixed(value, 2)}</td>
            <td className={tone(strategyPnl([leg], whatSpot, at, ivShift))}>{rupees(strategyPnl([leg], whatSpot, at, ivShift))}</td>
            <td><button className="settings-button" aria-label="Remove leg" onClick={() => setLegs((current) => current.filter((l) => l.id !== leg.id))}>✕</button></td>
          </tr>;
        })}</tbody></table></div>}
    </section>
    {legs.length > 0 && <>
      <section className="lab-metrics">
        <Metric label={premium >= 0 ? "Premium paid" : "Premium received"} value={rupees(Math.abs(premium)).replace("+", "")} />
        <Metric label="Max profit" value={ext.maxProfit == null ? "Unlimited" : rupees(ext.maxProfit)} tone={ext.maxProfit ?? 1} />
        <Metric label="Max loss" value={ext.maxLoss == null ? "Unlimited" : rupees(ext.maxLoss)} tone={ext.maxLoss ?? -1} />
        <Metric label="Breakevens" value={bes.length ? bes.map((b) => `${b.toFixed(0)} (${signed(((b / spot) - 1) * 100)}%)`).join(" · ") : "—"} />
        <Metric label="P&L at what-if" value={rupees(pnlNow)} tone={pnlNow} />
        <Metric label="Delta / Gamma" value={`${fixed(greeks.delta, 0)} / ${fixed(greeks.gamma, 3)}`} />
        <Metric label="Theta / day" value={rupees(greeks.theta)} tone={greeks.theta} />
        <Metric label="Vega / IV pt" value={rupees(greeks.vega)} />
        <Metric label="Rough margin" value={rupees(marginNeed).replace("+", "")} hint={`bought legs: premium; sold legs: ${margin}% of contract value less premium; spreads: max loss. Not the exchange SPAN.`} />
      </section>
      <section className="panel agent-panel">
        <div className="panel-title">What-if <small>move the spot, the date and IV; the dashed line is the value on the what-if date, the solid line at expiry ({dayLabel(firstExpiry)})</small></div>
        <div className="lab-sliders">
          <label>Spot {signed(spotShift)}% <b>{fixed(whatSpot, 1)}</b><input type="range" min={-15} max={15} step={0.25} value={spotShift} onChange={(e) => setSpotShift(Number(e.target.value))} /></label>
          <label>Days ahead {daysForward} <b>{at.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" })}</b><input type="range" min={0} max={maxDays} step={1} value={Math.min(daysForward, maxDays)} onChange={(e) => setDaysForward(Number(e.target.value))} /></label>
          <label>IV {signed(ivShift)} pts<input type="range" min={-15} max={15} step={0.5} value={ivShift} onChange={(e) => setIvShift(Number(e.target.value))} /></label>
          <label>Short margin %<input type="number" min={5} max={60} value={margin} onChange={(e) => setShortPct(Math.max(5, Math.min(60, Number(e.target.value) || 0)))} /></label>
          <button className="settings-button" onClick={() => { setSpotShift(0); setDaysForward(0); setIvShift(0); }}>Reset</button>
        </div>
        <PayoffChart curve={curve} spot={spot} whatSpot={whatSpot} breakevens={bes} />
        <div className="live-table-wrap"><table className="live-table agent-table lab-whatif"><thead><tr><th>Spot move</th><th>Spot</th><th>Today</th><th>{midDate.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" })}</th><th>Expiry</th></tr></thead>
          <tbody>{moves.map((m) => { const s = spot * (1 + m / 100); const a = strategyPnl(legs, s, now, ivShift), b = strategyPnl(legs, s, midDate, ivShift), e = expiryPnl(legs, s);
            return <tr key={m} className={m === 0 ? "bo-total" : undefined}><td>{signed(m, 0)}%</td><td>{fixed(s, 1)}</td><td className={tone(a)}>{rupees(a)}</td><td className={tone(b)}>{rupees(b)}</td><td className={tone(e)}>{rupees(e)}</td></tr>; })}</tbody></table></div>
      </section>
      {stockItm && c && c.delivery.length > 0 && <section className="panel agent-panel lab-warn">
        <div className="panel-title">Physical settlement <small>stock options settle by delivery</small></div>
        <p className="agent-sub bo-note">In-the-money long options, and every short option or future, attract a delivery margin from four sessions before expiry, blocked at the start of the day: {c.delivery.filter((s) => s.delivery_margin_pct).map((s) => `${s.stage} ${dayLabel(s.day)} ${s.delivery_margin_pct}%`).join(" · ")} of the delivery margin. The last session free of it is <b>{dayLabel(c.delivery.find((s) => s.stage === "E-5")?.day ?? c.expiry)}</b> (E-5). Brokers may ask earlier or square off on expiry day; see Expiry &amp; rollover.</p>
      </section>}
    </>}
    {c && <ChainTable chain={c} atm={atm} onAdd={addLeg} />}
  </>;
}

function Metric({ label, value, tone: t, hint }: { label: string; value: string; tone?: number; hint?: string }) {
  return <div className="panel lab-metric" title={hint}><span>{label}{hint ? " ⓘ" : ""}</span><b className={t == null ? "" : tone(t)}>{value}</b></div>;
}

function PayoffChart({ curve, spot, whatSpot, breakevens: bes }: { curve: { spot: number; expiry: number; at: number }[]; spot: number; whatSpot: number; breakevens: number[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  if (curve.length < 2) return null;
  const W = 720, H = 260, L = 64, R = 12, T = 12, B = 28;
  const xs = curve.map((p) => p.spot), ys = curve.flatMap((p) => [p.expiry, p.at]);
  const x0 = xs[0], x1 = xs[xs.length - 1];
  let y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  const pad = (y1 - y0) * 0.08 || 1; y0 -= pad; y1 += pad;
  const sx = (v: number) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
  const sy = (v: number) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const path = (key: "expiry" | "at") => curve.map((p, i) => `${i ? "L" : "M"}${sx(p.spot).toFixed(1)},${sy(p[key]).toFixed(1)}`).join("");
  const area = `M${sx(x0)},${sy(0)}` + curve.map((p) => `L${sx(p.spot).toFixed(1)},${sy(p.expiry).toFixed(1)}`).join("") + `L${sx(x1)},${sy(0)}Z`;
  const ticks = Array.from({ length: 5 }, (_, i) => y0 + ((y1 - y0) * i) / 4);
  const xticks = Array.from({ length: 7 }, (_, i) => x0 + ((x1 - x0) * i) / 6);
  const point = hover == null ? null : curve[hover];
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return;
    const value = x0 + (((e.clientX - box.left) / box.width) * W - L) / (W - L - R) * (x1 - x0);
    let best = 0;
    curve.forEach((p, i) => { if (Math.abs(p.spot - value) < Math.abs(curve[best].spot - value)) best = i; });
    setHover(best);
  };
  return <div className="lab-chart">
    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Payoff chart: P&L against spot at expiry and on the what-if date" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <defs><clipPath id="lab-up"><rect x={L} y={T} width={W - L - R} height={Math.max(0, sy(0) - T)} /></clipPath><clipPath id="lab-down"><rect x={L} y={sy(0)} width={W - L - R} height={Math.max(0, H - B - sy(0))} /></clipPath></defs>
      {ticks.map((v) => <g key={v}><line className="lab-grid" x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} /><text className="lab-axis" x={L - 6} y={sy(v) + 4} textAnchor="end">{rupees(v)}</text></g>)}
      {xticks.map((v) => <text key={v} className="lab-axis" x={sx(v)} y={H - 8} textAnchor="middle">{v.toFixed(0)}</text>)}
      <path className="lab-area-up" d={area} clipPath="url(#lab-up)" />
      <path className="lab-area-down" d={area} clipPath="url(#lab-down)" />
      <line className="lab-zero" x1={L} x2={W - R} y1={sy(0)} y2={sy(0)} />
      <path className="lab-line-expiry" d={path("expiry")} />
      <path className="lab-line-at" d={path("at")} />
      {spot >= x0 && spot <= x1 && <line className="lab-spot" x1={sx(spot)} x2={sx(spot)} y1={T} y2={H - B} />}
      {whatSpot !== spot && whatSpot >= x0 && whatSpot <= x1 && <line className="lab-whatspot" x1={sx(whatSpot)} x2={sx(whatSpot)} y1={T} y2={H - B} />}
      {bes.map((b) => <circle key={b} className="lab-be" cx={sx(b)} cy={sy(0)} r={4} />)}
      {point && <g><line className="lab-cross" x1={sx(point.spot)} x2={sx(point.spot)} y1={T} y2={H - B} /><circle className="lab-dot-expiry" cx={sx(point.spot)} cy={sy(point.expiry)} r={4} /><circle className="lab-dot-at" cx={sx(point.spot)} cy={sy(point.at)} r={4} /></g>}
    </svg>
    <div className="lab-legend"><span><i className="lab-key-expiry" /> at expiry</span><span><i className="lab-key-at" /> on the what-if date</span><span><i className="lab-key-spot" /> spot now</span>
      {point && <span className="lab-tip">Spot <b>{point.spot.toFixed(1)}</b> ({signed((point.spot / spot - 1) * 100)}%) · expiry <b className={tone(point.expiry)}>{rupees(point.expiry)}</b> · what-if date <b className={tone(point.at)}>{rupees(point.at)}</b></span>}</div>
  </div>;
}

function ChainTable({ chain, atm, onAdd }: { chain: Chain; atm: number; onAdd: (kind: "CE" | "PE", side: "BUY" | "SELL", strike: number) => void }) {
  const oi = (v?: number | null) => v == null ? "—" : v >= 10_000_000 ? `${(v / 10_000_000).toFixed(2)} Cr` : v >= 100_000 ? `${(v / 100_000).toFixed(1)} L` : v.toLocaleString("en-IN");
  const cell = (leg: ChainLeg | undefined, kind: "CE" | "PE", strike: number) => <>
    <td className="lab-buttons"><button className="lab-b" onClick={() => onAdd(kind, "BUY", strike)} disabled={!leg} aria-label={`Buy ${strike} ${kind}`}>B</button><button className="lab-s" onClick={() => onAdd(kind, "SELL", strike)} disabled={!leg} aria-label={`Sell ${strike} ${kind}`}>S</button></td>
    <td>{fixed(leg?.ltp, 2)}</td><td><small>{fixed(leg?.bid, 2)} / {fixed(leg?.ask, 2)}</small></td><td>{fixed(leg?.iv)}</td><td>{oi(leg?.oi)}</td></>;
  return <section className="panel agent-panel">
    <div className="panel-title">Chain {chain.underlying} {dayLabel(chain.expiry)} <small>stored snapshot {chain.as_of.slice(11, 16)} IST · {chain.rows.length} strikes · OI in shares · B / S adds a leg at the ask / bid</small></div>
    <div className="live-table-wrap"><table className="live-table agent-table lab-chain"><thead><tr><th /><th>CE LTP</th><th>Bid / Ask</th><th>IV</th><th>OI</th><th className="lab-strike">Strike</th><th /><th>PE LTP</th><th>Bid / Ask</th><th>IV</th><th>OI</th></tr></thead>
      <tbody>{chain.rows.map((r) => <tr key={r.strike} className={r.strike === atm ? "lab-atm" : undefined}>{cell(r.ce, "CE", r.strike)}<td className="lab-strike"><b>{r.strike}</b></td>{cell(r.pe, "PE", r.strike)}</tr>)}</tbody></table></div>
  </section>;
}

// ------------------------------------------------------------------------------------------------ expiry & rollover

function ExpiryView({ onOpen }: { onOpen: (underlying: string) => void }) {
  const { data, error } = useCachedJson<Exposure>("/api/expiry-exposure", 60_000);
  if (error && !data) return <div className="agent-error" role="alert">{error}</div>;
  if (!data) return <div className="agent-empty state-loading" role="status">Loading the lanes' stock options…</div>;
  const today = istNow();
  const root = (symbol: string) => symbol.split(":")[1]?.replace(/\d.*$/, "") ?? symbol;
  return <>
    <section className="panel agent-panel">
      <div className="panel-title">Physical-settlement calendar <small>stock options and futures settle by delivery; index options are cash settled</small></div>
      <p className="agent-sub bo-note">From E-4 the clearing corporation levies a delivery margin on in-the-money long options and on every future and short option: 10% / 25% / 45% / 70% of the delivery margin on E-4 / E-3 / E-2 / E-1, which brokers block at the <b>start</b> of the day (several ask earlier, and square off unconsented ITM positions on expiry day). The lanes now leave a stock option series on <b>E-{data.stock_option_exit_sessions}</b> (setting <code>stock_option_exit_sessions</code>); the margin-free rule is E-5. Margin figures below assume {Math.round(data.delivery_rate * 100)}% of contract value as the full delivery margin (stock VaR + ELM is roughly 15–30%; the broker's figure governs).</p>
      {Object.entries(data.schedules).map(([expiry, stages]) => <div key={expiry} className="lab-stages"><b>{dayLabel(expiry)} expiry</b>{stages.map((s) => <span key={s.stage} className={`lab-stage ${s.day === today ? "lab-stage-today" : s.day < today ? "lab-stage-past" : ""}`}>{s.stage} {dayLabel(s.day)} · {s.delivery_margin_pct}%</span>)}</div>)}
    </section>
    <section className="panel agent-panel">
      <div className="panel-title">By lane <small>open stock options and futures</small></div>
      {data.lanes.length === 0 ? <div className="agent-empty">No lane holds a stock option or future.</div> :
        <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Lane</th><th>Positions</th><th>Liable (ITM long, short, future)</th><th>Est. margin today</th><th>Est. margin on E-1</th></tr></thead>
          <tbody>{data.lanes.map((l) => <tr key={l.lane}><td><b>{l.lane}</b></td><td>{l.positions}</td><td>{l.liable}</td><td>{rupees(l.margin_today).replace("+", "")}</td><td>{rupees(l.margin_e1).replace("+", "")}</td></tr>)}</tbody></table></div>}
    </section>
    {data.rows.length > 0 && <section className="panel agent-panel">
      <div className="panel-title">Positions <small>nearest expiry first · click to open the underlying in the builder</small></div>
      <div className="live-table-wrap"><table className="live-table agent-table"><thead><tr><th>Lane</th><th>Contract</th><th>Qty</th><th>Avg</th><th>Last</th><th>Spot</th><th>ITM</th><th>Expiry</th><th>Sessions left</th><th>Lane exits by</th><th>Stage today</th><th>Contract value</th><th>Est. margin today</th></tr></thead>
        <tbody>{data.rows.map((r) => <tr key={`${r.lane}|${r.symbol}`} className="lab-click" onClick={() => onOpen(root(r.symbol))}>
          <td>{r.lane}</td><td><b>{r.symbol.replace(/^[A-Z]+:/, "")}</b></td><td>{r.quantity}</td><td>{fixed(r.average_price, 2)}</td><td>{fixed(r.last_price, 2)}</td><td>{fixed(r.spot, 2)}</td>
          <td className={r.itm ? "negative" : ""}>{r.kind === "FUT" ? "future" : r.itm ? "yes" : "no"}</td><td>{dayLabel(r.expiry)}</td><td>{r.sessions_to_expiry ?? "—"}</td>
          <td>{r.last_day ? dayLabel(r.last_day) : "—"}</td><td className={r.stage_pct ? "negative" : ""}>{r.stage_pct ? `${r.stage_pct}%` : "—"}</td>
          <td>{r.contract_value == null ? "—" : rupees(r.contract_value).replace("+", "")}</td><td>{r.margin_today ? rupees(r.margin_today).replace("+", "") : "—"}</td>
        </tr>)}</tbody></table></div>
    </section>}
  </>;
}
