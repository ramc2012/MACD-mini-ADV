import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { bracketIndex, bracketLetters, bracketTime, ladderRows, nearestPriceIndex, priceDigits, type LadderLevel } from "./profileLadderMath";
import "./MarketProfileLadder.css";

export type LadderProfile = {
  symbol: string; day: string; brackets: number; last: number | null;
  high: number | null; low: number | null; poc: number | null; vpoc?: number | null;
  vah: number | null; val: number | null; ib_high: number | null; ib_low: number | null;
  single_prints: number[]; levels: LadderLevel[];
  tick_size?: number; levels_total?: number; levels_sampled?: boolean;
  first_bracket?: number | null; partial_capture?: boolean;
};

const ROW_H = 28;
const OVERSCAN = 10;
const quantity = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function MarketProfileLadder({ profile }: { profile: LadderProfile }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const priorSession = useRef("");
  const anchor = useRef<{ price: number; offset: number } | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(360);
  const [expanded, setExpanded] = useState(false);
  const complete = profile.levels_sampled === false && (profile.levels_total ?? profile.levels.length) === profile.levels.length;
  const rows = useMemo(() => ladderRows(profile.levels, profile.tick_size, complete), [profile.levels, profile.tick_size, complete]);
  const letters = useMemo(() => bracketLetters(profile.levels, profile.brackets), [profile.levels, profile.brackets]);
  const digits = useMemo(() => priceDigits(profile.tick_size, profile.levels), [profile.tick_size, profile.levels]);
  const maxVolume = useMemo(() => profile.levels.reduce((max, row) => Math.max(max, Number.isFinite(row.volume) ? row.volume : 0), 1), [profile.levels]);
  const singlePrints = useMemo(() => new Set(profile.single_prints), [profile.single_prints]);
  const rowWidth = 92 + 68 + letters.length * 24 + 240;
  const gridColumns = `92px 68px repeat(${letters.length}, 24px) minmax(240px, 1fr)`;
  const totalLevels = profile.levels_total ?? profile.levels.length;
  const sampled = profile.levels_sampled === true || totalLevels > profile.levels.length;
  const partialCapture = profile.partial_capture === true || (profile.first_bracket ?? 0) > 0;
  const firstObserved = letters.length ? `${letters[0]} · ${bracketTime(bracketIndex(letters[0]))}` : "none";

  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const resize = () => setViewportHeight(el.clientHeight || 360);
    resize();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !rows.length) return;
    const session = `${profile.symbol}|${profile.day}`;
    const changed = priorSession.current !== session;
    priorSession.current = session;
    if (changed) anchor.current = null;
    const target = changed
      ? (profile.last ?? profile.poc ?? rows[Math.floor(rows.length / 2)].price)
      : (anchor.current?.price ?? profile.last ?? profile.poc);
    const index = nearestPriceIndex(rows, target);
    const offset = changed ? Math.floor(el.clientHeight / 2) : (anchor.current?.offset ?? 0);
    const next = Math.max(0, index * ROW_H - offset);
    if (Math.abs(el.scrollTop - next) >= ROW_H) el.scrollTop = next;
    setScrollTop(el.scrollTop);
  }, [rows, profile.symbol, profile.day, profile.last, profile.poc]);

  const jumpTo = (price: number | null) => {
    if (price == null || !Number.isFinite(price) || !scrollRef.current) return;
    scrollRef.current.scrollTop = Math.max(0, nearestPriceIndex(rows, price) * ROW_H - scrollRef.current.clientHeight / 2);
  };
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    const first = Math.min(rows.length - 1, Math.floor(el.scrollTop / ROW_H));
    if (first >= 0 && rows[first]) anchor.current = { price: rows[first].price, offset: first * ROW_H - el.scrollTop };
  };
  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const last = Math.min(rows.length, Math.ceil((scrollTop + viewportHeight) / ROW_H) + OVERSCAN);
  const tick = profile.tick_size && profile.tick_size > 0 ? profile.tick_size : 0.01;
  const at = (value: number | null | undefined, price: number) => value != null && Number.isFinite(value)
    && Math.abs(value - price) <= tick / 2 + 1e-7;

  return <div className={`mp-ladder${expanded ? " mp-ladder-expanded" : ""}`} aria-label={`TPO Market Profile for ${profile.symbol}`}>
    <div className="mp-ladder-toolbar">
      <div className="mp-ladder-source">
        <b>{profile.symbol.split(":").pop()}</b>
        <span>· {quantity.format(profile.levels.length)} / {quantity.format(totalLevels)} captured price levels</span>
        <span className={sampled ? "mp-ladder-sampled" : "mp-ladder-exact"}>{sampled ? "SAMPLED" : complete ? partialCapture ? "FULL CAPTURED LADDER" : "FULL LADDER" : "SOURCE RESOLUTION UNKNOWN"}</span>
      </div>
      <div className="mp-ladder-actions">
        <button type="button" onClick={() => jumpTo(profile.poc)} disabled={profile.poc == null}>POC</button>
        <button type="button" onClick={() => jumpTo(profile.vah)} disabled={profile.vah == null}>VAH</button>
        <button type="button" onClick={() => jumpTo(profile.val)} disabled={profile.val == null}>VAL</button>
        <button type="button" onClick={() => jumpTo(profile.last)} disabled={profile.last == null}>Last</button>
        <button type="button" className="mp-ladder-expand" onClick={() => setExpanded(value => !value)}
          aria-pressed={expanded}>{expanded ? "Exit expanded" : "Expand profile"}</button>
      </div>
    </div>
    {partialCapture && <div className="mp-ladder-coverage" role="status">
      Partial session capture · first observed {firstObserved}. POC and value area describe captured prints only; missing earlier brackets cannot be reconstructed.
    </div>}
    {sampled && <div className="mp-ladder-warning">Some price rows were omitted by the source. POC and value area use the complete server profile.</div>}
    <div className="mp-ladder-legend">Observed 30-minute TPO brackets · blue = captured TPO value area · bars = captured volume · {letters.length} observed</div>
    <div className="mp-ladder-xscroll">
      <div className="mp-ladder-grid" style={{ minWidth: rowWidth }}>
        <div className="mp-ladder-head" style={{ gridTemplateColumns: gridColumns }}>
          <span>Price</span><span></span>
          {letters.map((letter) => <span key={letter} title={`${letter} · ${bracketTime(bracketIndex(letter))}`}>{letter}</span>)}
          <span>Volume at price</span>
        </div>
        <div className="mp-ladder-scroll" ref={scrollRef} onScroll={onScroll} role="grid" aria-rowcount={rows.length}>
          <div className="mp-ladder-spacer" style={{ height: rows.length * ROW_H }}>
            {rows.slice(first, last).map((row, offset) => {
              const index = first + offset;
              const poc = at(profile.poc, row.price);
              const vpoc = at(profile.vpoc, row.price);
              const vah = at(profile.vah, row.price);
              const val = at(profile.val, row.price);
              const ibh = at(profile.ib_high, row.price);
              const ibl = at(profile.ib_low, row.price);
              const high = at(profile.high, row.price);
              const low = at(profile.low, row.price);
              const single = !partialCapture && profile.brackets >= 2 && (singlePrints.has(row.price) || row.tpo === 1);
              const inValue = profile.vah != null && profile.val != null && row.price <= profile.vah && row.price >= profile.val;
              const marks = [poc && "TPO POC", vpoc && "VPOC", vah && "VAH", val && "VAL",
                ibh && "IB high", ibl && "IB low", high && "Session high", low && "Session low", single && "Single TPO"].filter(Boolean);
              const badge = [poc && "POC", vpoc && "VPOC", vah && "VAH", val && "VAL",
                ibh && "IBH", ibl && "IBL", high && "H", low && "L", single && "SP"].filter(Boolean).join(" ");
              const title = `${row.price.toFixed(digits)} · ${row.tpo} TPO · ${quantity.format(row.volume)} volume${marks.length ? ` · ${marks.join(", ")}` : ""}`;
              return <div key={`${row.price}-${index}`} role="row" aria-rowindex={index + 1}
                className={`mp-ladder-row${index % 2 ? " is-alt" : ""}${inValue ? " in-value" : ""}${poc ? " is-poc" : ""}${vpoc ? " is-vpoc" : ""}${vah ? " is-vah" : ""}${val ? " is-val" : ""}${row.empty ? " is-empty" : ""}`}
                style={{ top: index * ROW_H, gridTemplateColumns: gridColumns }} title={title}>
                <span className="mp-ladder-price" role="gridcell">{row.price.toFixed(digits)}</span>
                <span className="mp-ladder-marker" role="gridcell">{badge}</span>
                {letters.map((letter) => <span key={letter} role="gridcell"
                  className={`mp-ladder-tpo${row.letters.includes(letter) ? " filled" : ""}`}>{row.letters.includes(letter) ? letter : ""}</span>)}
                <span className="mp-ladder-volume" role="gridcell">
                  <span className="mp-ladder-volume-bar" style={{ width: `${Math.max(0, Math.min(100, row.volume / maxVolume * 100))}%` }} />
                  <span className="mp-ladder-volume-text">{row.volume ? quantity.format(row.volume) : ""}</span>
                </span>
              </div>;
            })}
          </div>
        </div>
      </div>
    </div>
  </div>;
}
