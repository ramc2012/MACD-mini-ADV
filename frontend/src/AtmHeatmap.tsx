import { useEffect, useMemo, useState } from "react";
import { requestJson } from "./requestJson";
import { API_TOKEN, API_URL } from "./runtime";

type Changes = Record<string, number | null>;
type Leg = { symbol: string; strike: number; price: number | null; change: Changes; comparison?: Changes; partial?: Record<string, string> };
type Row = {
  underlying: string; kind?: "index" | "sector" | "stock"; sector?: string; spot_symbol: string; spot: number | null;
  spot_change: Changes; spot_comparison?: Changes; spot_partial?: Record<string, string>;
  strike: number; expiry: string; ce?: Leg; pe?: Leg;
};
type Payload = { as_of: string; periods: string[]; references: Record<string, string>; rows: Row[] };
type Instrument = "CE" | "PE" | "SPOT";
type Scope = "ALL" | "INDEX" | "STOCK";
const DAY_BASES = [["1D", "Day open"], ["PREV_CLOSE", "Previous close"]] as const;
type DayBasis = typeof DAY_BASES[number][0];

const INDICES = new Set(["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"]);
const PERIOD_LABEL: Record<string, string> = { "1H": "This hour", "4H": "This 4-hour block", "1D": "From day open", PREV_CLOSE: "From previous close", "1W": "This week", "1M": "This month" };
// Colour saturates at these moves: option premiums swing far more than their underlying, and longer periods more.
const OPTION_CAP: Record<string, number> = { "1H": 15, "4H": 30, "1D": 50, "1W": 100, "1M": 150 };
const SPOT_CAP: Record<string, number> = { "1H": 0.75, "4H": 1.5, "1D": 2.5, "1W": 5, "1M": 8 };

type Tile = { name: string; kind: "index" | "sector" | "stock"; move: number | null; price: number | null; reference?: number | null; detail: string; since?: string; symbol: string };

function tileStyle(move: number | null, cap: number): React.CSSProperties {
  if (move === null || move === 0) return {};
  const strength = 0.18 + Math.min(1, Math.abs(move) / cap) * 0.72;
  return { background: move >= 0 ? `rgba(38, 166, 91, ${strength})` : `rgba(229, 72, 77, ${strength})` };
}
const pct = (move: number | null) => (move === null ? "—" : `${move > 0 ? "+" : ""}${move.toFixed(1)}%`);
const clock = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Kolkata", dateStyle: "short", timeStyle: "medium" });
const timestamp = (value: string) => `${clock.format(new Date(value))} IST`;
const money = (value: number | null) => (value === null ? "—" : value >= 1000 ? value.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : value.toFixed(2));

/** Period-to-date moves of each underlying's ATM call, ATM put or spot, as one tile per underlying. */
export function AtmHeatmap() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [period, setPeriod] = useState("1D");
  const [instrument, setInstrument] = useState<Instrument>("CE");
  const [scope, setScope] = useState<Scope>("ALL");
  const [sector, setSector] = useState("");
  const [dayBasis, setDayBasis] = useState<DayBasis>("1D");
  const [query, setQuery] = useState("");
  const [order, setOrder] = useState<"DESC" | "ASC" | "NAME">("DESC");
  // Empty = live. A past date replays the map at that day's close with the ATM contracts of that day.
  const [asOf, setAsOf] = useState("");
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const monthStart = `${today.slice(0, 8)}01`;

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const controller = new AbortController();
    setData(null); setError("");
    const load = () => void requestJson<Payload>(`${API_URL}/api/heatmap/atm${asOf ? `?as_of=${asOf}` : ""}`, { headers, signal: controller.signal })
      .then((payload) => { setData(payload); setError(""); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unavailable"); });
    load();
    const timer = asOf ? undefined : setInterval(load, 60_000);
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [asOf]);

  const metric = period === "1D" ? dayBasis : period;
  const sectors = useMemo(() => [...new Set((data?.rows || []).filter(row => row.kind === "stock")
    .map(row => row.sector).filter((name): name is string => !!name))].sort((a, b) => a.localeCompare(b)), [data]);
  const chooseScope = (value: Scope) => { setScope(value); if (value !== "STOCK") setSector(""); };
  const chooseSector = (value: string) => { setSector(value); if (value) setScope("STOCK"); };

  const tiles = useMemo(() => {
    const out: Tile[] = [];
    for (const row of data?.rows || []) {
      const kind = row.kind || (INDICES.has(row.underlying) ? "index" : "stock");
      if (scope !== "ALL" && (scope === "INDEX") !== (kind !== "stock")) continue;
      if (sector && (kind !== "stock" || row.sector !== sector)) continue;
      if (!row.underlying.toLowerCase().includes(query.trim().toLowerCase())) continue;
      if (instrument === "SPOT") {
        out.push({ name: row.underlying, kind, move: row.spot_change?.[metric] ?? null, price: row.spot, symbol: row.spot_symbol,
          reference: row.spot_comparison?.[metric], detail: kind === "sector" ? "index" : "spot", since: row.spot_partial?.[metric] });
      } else {
        const leg = instrument === "CE" ? row.ce : row.pe;
        if (!leg) continue;
        out.push({ name: row.underlying, kind, move: leg.change?.[metric] ?? null, price: leg.price, symbol: leg.symbol,
          reference: leg.comparison?.[metric], detail: `${leg.strike} ${instrument} · ${row.expiry.slice(5)}`, since: leg.partial?.[metric] });
      }
    }
    const value = (tile: Tile) => tile.move ?? (order === "ASC" ? Infinity : -Infinity);
    return out.sort((a, b) => order === "NAME" ? a.name.localeCompare(b.name) : order === "DESC" ? value(b) - value(a) : value(a) - value(b));
  }, [data, metric, instrument, scope, sector, query, order]);

  const cap = (instrument === "SPOT" ? SPOT_CAP : OPTION_CAP)[period];
  const indexTiles = tiles.filter((tile) => tile.kind === "index");
  const sectorTiles = tiles.filter((tile) => tile.kind === "sector");
  const stockTiles = tiles.filter((tile) => tile.kind === "stock");
  const rising = tiles.filter((tile) => (tile.move ?? 0) > 0).length;
  const withMove = tiles.filter((tile) => tile.move !== null).length;

  const hasPartial = tiles.some(tile => tile.since);
  const basisLabel = DAY_BASES.find(([key]) => key === metric)?.[1];

  const grid = (list: Tile[]) => <div className="heat-tiles">{list.map((tile) =>
    <div key={tile.name} className={`heat-tile${tile.move === null ? " heat-empty" : ""}`} style={tileStyle(tile.move, cap)}
      title={[tile.symbol, basisLabel && (tile.reference != null ? `${basisLabel} ₹${money(tile.reference)}` : `${basisLabel} unavailable`),
        tile.since && `First stored trade ${timestamp(tile.since)}`].filter(Boolean).join(" · ")}>
      <div className="heat-tile-name">{tile.name}</div>
      <div className="heat-tile-move">{pct(tile.move)}{tile.since ? <span className="heat-partial" aria-label="reference from first stored trade after period start">*</span> : null}</div>
      <div className="heat-tile-detail">{tile.price === null ? "—" : `₹${money(tile.price)}`} · {tile.detail}</div>
    </div>)}</div>;

  return <section className="panel atm-heatmap">
    <div className="heatmap-toolbar">
      <div className="risk-tabs" role="group" aria-label="Period">{(data?.periods || ["1H", "4H", "1D", "1W", "1M"]).map((key) =>
        <button key={key} aria-pressed={period === key} onClick={() => setPeriod(key)}>{key}</button>)}</div>
      {period === "1D" && <div className="risk-tabs" role="group" aria-label="Daily comparison">
        {DAY_BASES.map(([key, label]) => <button key={key} aria-pressed={dayBasis === key} onClick={() => setDayBasis(key)}>{label}</button>)}
      </div>}
      <div className="risk-tabs" role="group" aria-label="Instrument">{([["CE", "ATM CE"], ["PE", "ATM PE"], ["SPOT", "Spot"]] as const).map(([key, label]) =>
        <button key={key} aria-pressed={instrument === key} onClick={() => setInstrument(key)}>{label}</button>)}</div>
      <div className="risk-tabs" role="group" aria-label="Universe">{(["ALL", "INDEX", "STOCK"] as const).map((key) =>
        <button key={key} aria-pressed={scope === key} onClick={() => chooseScope(key)}>{key === "ALL" ? "All" : key === "INDEX" ? "Indices & sectors" : "Stocks"}</button>)}</div>
      <select className="heatmap-search" value={sector} onChange={(event) => chooseSector(event.target.value)} aria-label="Sector">
        <option value="">All sectors</option>
        {sectors.map(name => <option key={name} value={name}>{name}</option>)}
      </select>
      <select className="heatmap-search" value={order} onChange={(event) => setOrder(event.target.value as typeof order)} aria-label="Sort">
        <option value="DESC">Biggest gain first</option><option value="ASC">Biggest loss first</option><option value="NAME">A–Z</option>
      </select>
      <input className="heatmap-search" aria-label="Filter underlying" placeholder="Filter underlying" value={query} onChange={(event) => setQuery(event.target.value)} />
      <div className="risk-tabs" role="group" aria-label="As of">
        <button aria-pressed={!asOf} onClick={() => setAsOf("")}>Live</button>
        <button aria-pressed={asOf === monthStart} onClick={() => setAsOf(monthStart)} title="The first session of this month, at its close">Month start</button>
      </div>
      <input className="heatmap-search" type="date" aria-label="As of date" max={today} value={asOf} onChange={(event) => setAsOf(event.target.value >= today ? "" : event.target.value)} />
    </div>
    <div className="heatmap-meta">{data
      ? <>{asOf && <b className="heat-asof">Replay · </b>}{PERIOD_LABEL[metric]} · {instrument === "SPOT" ? "underlying" : `ATM ${instrument} premium`}
        {data.references[metric] && <> · {timestamp(data.references[metric])} → {timestamp(data.as_of)}</>}
        {" · "}{rising} of {withMove} up{hasPartial && " · * first stored trade came later"}</>
      : error || "Loading…"}</div>
    {indexTiles.length > 0 && <><h3 className="heat-group">Indices</h3>{grid(indexTiles)}</>}
    {sectorTiles.length > 0 && <><h3 className="heat-group">Sectors &amp; market</h3>{grid(sectorTiles)}</>}
    {stockTiles.length > 0 && <><h3 className="heat-group">{sector || "Stocks"}</h3>{grid(stockTiles)}</>}
    {data && !tiles.length && <p className="heatmap-meta">No instruments match these filters.</p>}
  </section>;
}
