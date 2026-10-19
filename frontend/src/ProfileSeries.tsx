import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { rowContains, rowOverlaps } from "./profileLadderMath";

type SeriesRow = { price: number; volume: number; tpo: number; letters: string };
export type SeriesColumn = {
  kind: "month" | "week" | "day" | "today"; key: string; label: string; developing?: boolean;
  from: string; to: string; sessions: number; source: string;
  open: number | null; close: number | null; high: number | null; low: number | null;
  poc: number | null; vah: number | null; val: number | null; volume: number; rows: SeriesRow[];
};
type Series = {
  symbol: string; as_of: string; row_size: number; columns: SeriesColumn[]; notes: string[];
  stored_sessions: number; last: number | null;
  row_scale?: { basis: string; atr: number | null; atr_sessions: number; rows_per_atr: number } | null;
  closing_auction?: { price: number; volume: number; at: string } | null;
};

const ROW_H = 16;
const AXIS_W = 72;
const WIDTH: Record<SeriesColumn["kind"], number> = { month: 190, week: 150, day: 176, today: 190 };
const KIND: Record<SeriesColumn["kind"], string> = { month: "Month", week: "Week", day: "Day", today: "Today" };
const qty = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

/** Previous month, earlier weeks, this week, the last sessions and today, on one ATR row grid. */
export function ProfileSeriesChart({ symbol }: { symbol: string }) {
  const [series, setSeries] = useState<Series>();
  const [error, setError] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const positioned = useRef("");

  useEffect(() => {
    if (!symbol) return;
    let stopped = false;
    const load = () => {
      void fetch(`${API_URL}/api/mp/profile-series/${encodeURIComponent(symbol)}`,
        { headers: API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined })
        .then(async (response) => {
          if (!response.ok) throw new Error((await response.json()).detail || "Profile series unavailable");
          return response.json() as Promise<Series>;
        })
        .then((data) => { if (!stopped) { setSeries(data); setError(""); } })
        .catch((reason) => { if (!stopped) setError(reason instanceof Error ? reason.message : "Profile series unavailable"); });
    };
    load();
    const timer = window.setInterval(load, 30_000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [symbol]);

  const grid = useMemo(() => {
    if (!series?.columns.length) return null;
    const step = series.row_size;
    let top = -Infinity;
    let bottom = Infinity;
    for (const column of series.columns) for (const row of column.rows) {
      top = Math.max(top, row.price);
      bottom = Math.min(bottom, row.price);
    }
    if (!Number.isFinite(top)) return null;
    const count = Math.round((top - bottom) / step) + 1;
    const prices = Array.from({ length: count }, (_, i) => Math.round((top - i * step) * 1e6) / 1e6);
    const byColumn = series.columns.map((column) => {
      const rows = new Map(column.rows.map((row) => [row.price.toFixed(6), row]));
      const maxVolume = Math.max(1, ...column.rows.map((row) => row.volume));
      const maxTpo = Math.max(1, ...column.rows.map((row) => row.tpo));
      return { column, rows, maxVolume, maxTpo };
    });
    const digits = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
    return { step, prices, byColumn, digits };
  }, [series]);

  // Open at the newest columns, centred on the last price -- once per symbol.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !grid || positioned.current === symbol) return;
    positioned.current = symbol;
    el.scrollLeft = el.scrollWidth;
    const anchor = series?.last ?? series?.columns[series.columns.length - 1]?.close ?? null;
    const index = anchor == null ? grid.prices.length / 2
      : grid.prices.findIndex((price) => price <= anchor + 1e-9);
    el.scrollTop = Math.max(0, (index < 0 ? grid.prices.length / 2 : index) * ROW_H - el.clientHeight / 2);
  }, [grid, symbol, series]);

  if (error) return <div className="chart-empty">{error}</div>;
  if (!series || !grid) return <div className="chart-empty">Loading profiles…</div>;
  const template = `${AXIS_W}px ${grid.byColumn.map(({ column }) => `${WIDTH[column.kind]}px`).join(" ")}`;
  const last = series.last;
  const scale = series.row_scale;

  return <div className="ps">
    <div className="ps-note">
      <span>rows ₹{grid.step}{scale?.atr != null ? ` · ${scale.atr_sessions}-day ATR ₹${scale.atr} ÷ ${scale.rows_per_atr}` : ""}</span>
      <span>{series.columns.length} profiles · scroll ← for older · shaded = value area · ▸ = POC</span>
      {series.notes.map((note) => <span key={note} className="ps-warn">{note}</span>)}
      {series.closing_auction && <span title="Closing-auction match, kept out of the profile and delta">
        closing auction ₹{series.closing_auction.price} · {qty.format(series.closing_auction.volume)}</span>}
    </div>
    <div className="ps-scroll" ref={scrollRef}>
      <div className="ps-grid" style={{ gridTemplateColumns: template }}>
        <div className="ps-head ps-axis-head">Price</div>
        {grid.byColumn.map(({ column }) => <div key={`${column.kind}-${column.key}`}
          className={`ps-head ps-${column.kind}${column.developing ? " developing" : ""}`}
          title={`${column.from} → ${column.to} · ${column.sessions} session${column.sessions > 1 ? "s" : ""} · ${column.source}`}>
          <b>{column.label}</b>
          <span>{KIND[column.kind]}{column.developing ? " · developing" : ""}{column.sessions > 1 ? ` · ${column.sessions}d` : ""}</span>
          <span>POC {column.poc ?? "—"} · VA {column.val ?? "—"}–{column.vah ?? "—"}</span>
        </div>)}
        {grid.prices.map((price) => <Row key={price} price={price} grid={grid} last={last} />)}
      </div>
    </div>
  </div>;
}

function Row({ price, grid, last }: {
  price: number; last: number | null;
  grid: { step: number; digits: number; byColumn: { column: SeriesColumn; rows: Map<string, SeriesRow>; maxVolume: number; maxTpo: number }[] };
}) {
  const isLast = rowContains(price, grid.step, last);
  return <>
    <div className={`ps-axis${isLast ? " last" : ""}`}>{price.toFixed(grid.digits)}</div>
    {grid.byColumn.map(({ column, rows, maxVolume, maxTpo }) => {
      const row = rows.get(price.toFixed(6));
      const inRange = column.high != null && column.low != null && rowOverlaps(price, grid.step, column.low, column.high);
      const inValue = column.vah != null && column.val != null && rowOverlaps(price, grid.step, column.val, column.vah);
      const poc = rowContains(price, grid.step, column.poc);
      const width = row ? Math.max(row.volume / maxVolume, row.volume ? 0.02 : 0) * 100
        : 0;
      const tpoWidth = row && !row.volume ? (row.tpo / maxTpo) * 100 : 0;
      return <div key={`${column.kind}-${column.key}`}
        className={`ps-cell${inRange ? "" : " out"}${inValue ? " va" : ""}${poc ? " poc" : ""}`}
        title={row ? `${price.toFixed(grid.digits)} · ${column.label} · ${row.tpo} TPO · ${qty.format(row.volume)} vol` : undefined}>
        {row && <span className="ps-bar" style={{ width: `${width || tpoWidth}%` }} />}
        {poc && <i className="ps-poc">▸</i>}
        {row && (column.kind === "day" || column.kind === "today")
          ? <span className="ps-letters">{row.letters}</span>
          : row && row.tpo ? <span className="ps-count">{row.tpo}</span> : null}
      </div>;
    })}
  </>;
}
