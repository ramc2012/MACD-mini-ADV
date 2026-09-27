import { useEffect, useMemo, useRef, type CSSProperties, type ReactNode } from "react";

/* ------------------------------------------------------------------ theme */

const PANEL = "#0b1018";
const CANVAS = "#090d14";
const BORDER = "#202a38";
const BORDER_HI = "#263144";
const TEXT = "#dce5f1";
const DIM = "#8796aa";
const DIMMER = "#687990";
const GRID = "#141d29";
const BUY = "#22c893";
const SELL = "#f15b6c";
const ACCENT = "#4d91ff";
const GOLD = "#e5c85a";
const VA_EDGE = "#7f9ec4";      // blue-grey value-area band edge
const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";

const SPAN = 20;          // ticks either side of the anchor
const ROW_H = 15;         // px per price rung
const LADDER_H = 430;     // px of visible ladder before internal scroll

const SCROLL_CSS = `
.dom-scroll::-webkit-scrollbar{width:6px;height:6px}
.dom-scroll::-webkit-scrollbar-thumb{background:#263144;border-radius:3px}
.dom-scroll::-webkit-scrollbar-track{background:transparent}
`;

/* ------------------------------------------------------------------ types */

export type DomQuote = {
  bid: number | null;
  ask: number | null;
  bid_qty: number | null;
  ask_qty: number | null;
  total_buy_qty: number | null;
  total_sell_qty: number | null;
  last: number | null;
  oi: number | null;
  avg_trade_price: number | null;
};

export type ProfileLevel = { price: number; tpo: number; letters: string; volume: number };

export type ProfileShape = {
  poc: number | null;
  vah: number | null;
  val: number | null;
  ib_high: number | null;
  ib_low: number | null;
  high: number | null;
  low: number | null;
  last: number | null;
  levels: ProfileLevel[];
};

export type TapePrint = { t: number; p: number; s: number; side: number };

/* ------------------------------------------------------------- formatting */

const qtyFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const timeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

const qty = (v: number | null | undefined): string => (v === null || v === undefined || !isFinite(v) ? "—" : qtyFmt.format(Math.round(v)));
const clock = (t: number): string => timeFmt.format(new Date(t * 1000));
const decimalsFor = (step: number): number => {
  const s = step.toString();
  const dot = s.indexOf(".");
  return dot < 0 ? 0 : Math.min(6, s.length - dot - 1);
};
const roundTo = (v: number, dp: number): number => Number(v.toFixed(dp));
const firstNumber = (candidates: Array<number | null | undefined>): number | null => {
  for (const c of candidates) if (typeof c === "number" && isFinite(c) && c > 0) return c;
  return null;
};
const num = (v: number | null | undefined): number => (typeof v === "number" && isFinite(v) ? v : 0);

/* ------------------------------------------------------------------ shell */

function Shell({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return <div style={{
    display: "flex", flexDirection: "column", background: PANEL, border: `1px solid ${BORDER}`,
    borderRadius: 4, color: TEXT, fontFamily: FONT, fontSize: 10, fontVariantNumeric: "tabular-nums",
    overflow: "hidden", minWidth: 0,
  }}>
    <style>{SCROLL_CSS}</style>
    <div style={{
      display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8,
      padding: "6px 8px", borderBottom: `1px solid ${BORDER}`, background: CANVAS,
    }}>
      <span style={{ fontSize: 10, letterSpacing: 0.7, color: DIM, textTransform: "uppercase" }}>{title}</span>
      {note && <span style={{ fontSize: 9, color: DIMMER }}>{note}</span>}
    </div>
    {children}
  </div>;
}

function Empty({ text }: { text: string }) {
  return <div style={{
    display: "flex", alignItems: "center", justifyContent: "center", padding: "26px 10px",
    color: DIMMER, fontSize: 10, letterSpacing: 0.4, background: CANVAS,
  }}>{text}</div>;
}

/* ------------------------------------------------------------- dom ladder */

type Rung = {
  price: number;
  label: string;
  isBid: boolean;
  isAsk: boolean;
  isLast: boolean;
  isPoc: boolean;
  isVah: boolean;
  isVal: boolean;
  isIbHigh: boolean;
  isIbLow: boolean;
  inValue: boolean;
};

export function DomLadder({ dom, profile, tickSize, lastPrice }: {
  dom: DomQuote | null;
  profile: ProfileShape | null;
  tickSize: number;
  lastPrice?: number | null;
}) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const markerRef = useRef<HTMLDivElement | null>(null);

  const view = useMemo(() => {
    if (!dom) return null;
    const step = tickSize > 0 ? tickSize : 0.05;
    const dp = decimalsFor(step);
    const anchor = firstNumber([lastPrice, dom.last, dom.ask, dom.bid, profile?.last, profile?.poc]);
    if (anchor === null) return null;

    const near = (a: number, b: number | null | undefined): boolean =>
      typeof b === "number" && isFinite(b) && Math.abs(a - b) < step / 2;

    const last = firstNumber([lastPrice, dom.last]);
    const vah = profile && typeof profile.vah === "number" ? profile.vah : null;
    const val = profile && typeof profile.val === "number" ? profile.val : null;
    const centre = Math.round(anchor / step);
    const rungs: Rung[] = [];
    for (let i = SPAN; i >= -SPAN; i--) {
      const price = roundTo((centre + i) * step, dp);
      rungs.push({
        price,
        label: price.toFixed(dp),
        isBid: near(price, dom.bid),
        isAsk: near(price, dom.ask),
        isLast: near(price, last),
        isPoc: near(price, profile?.poc),
        isVah: near(price, vah),
        isVal: near(price, val),
        isIbHigh: near(price, profile?.ib_high),
        isIbLow: near(price, profile?.ib_low),
        inValue: vah !== null && val !== null && price <= vah + step / 2 && price >= val - step / 2,
      });
    }
    const maxQty = Math.max(num(dom.bid_qty), num(dom.ask_qty), 1);
    const spread = typeof dom.ask === "number" && typeof dom.bid === "number" ? roundTo(dom.ask - dom.bid, dp) : null;
    const totalBuy = num(dom.total_buy_qty);
    const totalSell = num(dom.total_sell_qty);
    const totalBook = totalBuy + totalSell;
    return { rungs, maxQty, dp, spread, totalBuy, totalSell, totalBook, last };
  }, [dom, profile, tickSize, lastPrice]);

  useEffect(() => {
    const box = bodyRef.current;
    const row = markerRef.current;
    if (!box || !row) return;
    box.scrollTop = Math.max(0, row.offsetTop - box.clientHeight / 2 + row.offsetHeight / 2);
  }, [view?.last, view?.rungs.length]);

  if (!dom || !view) {
    return <Shell title="DOM Ladder" note="depth">
      <Empty text="No depth yet — waiting for a quote on this contract." />
    </Shell>;
  }

  const buyPct = view.totalBook > 0 ? (view.totalBuy / view.totalBook) * 100 : 50;
  const cell: CSSProperties = { position: "relative", height: ROW_H, display: "flex", alignItems: "center", overflow: "hidden" };
  const head: CSSProperties = { fontSize: 9, color: DIMMER, letterSpacing: 0.6, textTransform: "uppercase" };
  const grid = "1fr 62px 1fr 30px";

  return <Shell
    title="DOM Ladder"
    note={`${typeof dom.bid === "number" ? dom.bid.toFixed(view.dp) : "—"} × ${typeof dom.ask === "number" ? dom.ask.toFixed(view.dp) : "—"}${view.spread !== null ? `  spread ${view.spread.toFixed(view.dp)}` : ""}`}
  >
    <div style={{ display: "grid", gridTemplateColumns: grid, gap: 0, padding: "3px 6px", background: CANVAS, borderBottom: `1px solid ${BORDER}` }}>
      <span style={{ ...head, textAlign: "right", paddingRight: 6 }}>Bid qty</span>
      <span style={{ ...head, textAlign: "center" }}>Price</span>
      <span style={{ ...head, paddingLeft: 6 }}>Ask qty</span>
      <span style={{ ...head, textAlign: "right" }}>Lvl</span>
    </div>

    <div ref={bodyRef} className="dom-scroll" style={{ maxHeight: LADDER_H, overflowY: "auto", background: CANVAS, padding: "0 6px" }}>
      {view.rungs.map(r => {
        const bidW = r.isBid ? Math.max(6, (num(dom.bid_qty) / view.maxQty) * 100) : 0;
        const askW = r.isAsk ? Math.max(6, (num(dom.ask_qty) / view.maxQty) * 100) : 0;
        const tag = r.isPoc ? "POC" : r.isVah ? "VAH" : r.isVal ? "VAL" : r.isIbHigh ? "IBH" : r.isIbLow ? "IBL" : "";
        const tagColor = r.isPoc ? GOLD : r.isVah || r.isVal ? VA_EDGE : ACCENT;
        const rowStyle: CSSProperties = {
          display: "grid", gridTemplateColumns: grid, alignItems: "stretch",
          borderBottom: r.isVal ? `1px solid ${VA_EDGE}` : r.isIbLow ? `1px dashed ${ACCENT}` : `1px solid ${GRID}`,
          borderTop: r.isVah ? `1px solid ${VA_EDGE}` : r.isIbHigh ? `1px dashed ${ACCENT}` : undefined,
          background: r.isLast ? "rgba(220,229,241,0.07)" : r.isPoc ? "rgba(229,200,90,0.10)" : r.inValue ? "rgba(77,145,255,0.045)" : "transparent",
          boxShadow: r.isPoc ? `inset 3px 0 0 ${GOLD}, inset -3px 0 0 ${GOLD}` : undefined,
        };
        return <div key={r.label} ref={r.isLast ? markerRef : undefined} style={rowStyle}>
          <div style={{ ...cell, justifyContent: "flex-end", paddingRight: 6 }}>
            {r.isBid ? <>
              <div style={{ position: "absolute", right: 0, top: 2, bottom: 2, width: `${bidW}%`, background: "rgba(241,91,108,0.28)", borderRight: `2px solid ${SELL}` }} />
              <span style={{ position: "relative", fontSize: 9, color: DIMMER, marginRight: "auto", paddingLeft: 2, letterSpacing: 0.5 }}>BID</span>
              <span style={{ position: "relative", fontSize: 10, fontWeight: 700, color: SELL }}>{qty(dom.bid_qty)}</span>
            </> : <span style={{ color: "#22303f", fontSize: 9 }}>·</span>}
          </div>

          <div style={{
            ...cell, justifyContent: "center",
            borderLeft: `1px solid ${BORDER}`, borderRight: `1px solid ${BORDER}`,
          }}>
            {r.isLast
              ? <span style={{ background: ACCENT, color: "#06101c", fontWeight: 700, fontSize: 10, padding: "1px 6px", borderRadius: 3, letterSpacing: 0.2 }}>{r.label}</span>
              : <span style={{ fontSize: 10, color: r.isPoc ? GOLD : r.isBid ? SELL : r.isAsk ? BUY : r.inValue ? TEXT : DIM }}>{r.label}</span>}
          </div>

          <div style={{ ...cell, justifyContent: "flex-start", paddingLeft: 6 }}>
            {r.isAsk ? <>
              <div style={{ position: "absolute", left: 0, top: 2, bottom: 2, width: `${askW}%`, background: "rgba(34,200,147,0.28)", borderLeft: `2px solid ${BUY}` }} />
              <span style={{ position: "relative", fontSize: 10, fontWeight: 700, color: BUY }}>{qty(dom.ask_qty)}</span>
              <span style={{ position: "relative", fontSize: 9, color: DIMMER, marginLeft: "auto", paddingRight: 2, letterSpacing: 0.5 }}>ASK</span>
            </> : <span style={{ color: "#22303f", fontSize: 9 }}>·</span>}
          </div>

          <div style={{ ...cell, justifyContent: "flex-end" }}>
            {tag && <span style={{ fontSize: 8, letterSpacing: 0.5, color: tagColor, fontWeight: 700 }}>{tag}</span>}
          </div>
        </div>;
      })}
    </div>

    <div style={{ padding: "6px 8px", borderTop: `1px solid ${BORDER}`, background: PANEL, display: "flex", flexDirection: "column", gap: 5 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: DIMMER, letterSpacing: 0.5 }}>
        <span>Book pressure · touch only, no ladder depth in this feed</span>
        <span>{buyPct.toFixed(1)}% buy</span>
      </div>
      <div style={{ display: "flex", height: 8, borderRadius: 2, overflow: "hidden", background: GRID, border: `1px solid ${BORDER_HI}` }}>
        <div style={{ width: `${buyPct}%`, background: BUY }} />
        <div style={{ width: `${100 - buyPct}%`, background: SELL }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10 }}>
        <span style={{ color: BUY, fontWeight: 700 }}>{qty(view.totalBuy)}<span style={{ color: DIMMER, fontWeight: 400, marginLeft: 4 }}>total buy</span></span>
        <span style={{ color: DIM }}>OI {qty(dom.oi)} · ATP {typeof dom.avg_trade_price === "number" ? dom.avg_trade_price.toFixed(view.dp) : "—"}</span>
        <span style={{ color: SELL, fontWeight: 700 }}><span style={{ color: DIMMER, fontWeight: 400, marginRight: 4 }}>total sell</span>{qty(view.totalSell)}</span>
      </div>
    </div>
  </Shell>;
}

/* ---------------------------------------------------------- time & sales */

export function TimeAndSales({ tape, tickSize, height = 300 }: {
  tape: TapePrint[];
  tickSize: number;
  height?: number;
}) {
  const dp = decimalsFor(tickSize > 0 ? tickSize : 0.05);

  const stats = useMemo(() => {
    let maxSize = 0;
    let buyVol = 0;
    let sellVol = 0;
    for (const p of tape) {
      if (p.s > maxSize) maxSize = p.s;
      if (p.side > 0) buyVol += p.s;
      else if (p.side < 0) sellVol += p.s;
    }
    return { maxSize, buyVol, sellVol };
  }, [tape]);

  const head: CSSProperties = { fontSize: 9, color: DIMMER, letterSpacing: 0.6, textTransform: "uppercase" };
  const grid = "62px 1fr 1fr 34px";

  return <Shell title="Time & Sales" note={`${tape.length} prints`}>
    <div style={{ display: "grid", gridTemplateColumns: grid, padding: "3px 8px", background: CANVAS, borderBottom: `1px solid ${BORDER}` }}>
      <span style={head}>Time</span>
      <span style={{ ...head, textAlign: "right" }}>Price</span>
      <span style={{ ...head, textAlign: "right" }}>Size</span>
      <span style={{ ...head, textAlign: "right" }}>Side</span>
    </div>

    <div className="dom-scroll" style={{ height, overflowY: "auto", background: CANVAS }}>
      {tape.length === 0
        ? <Empty text="No prints on the tape yet." />
        : tape.map((p, i) => {
          const rel = Math.min(1, p.s / Math.max(stats.maxSize, 1));
          const tone = p.side > 0 ? BUY : p.side < 0 ? SELL : DIM;
          const rgb = p.side > 0 ? "34,200,147" : p.side < 0 ? "241,91,108" : "135,150,170";
          const heavy = rel >= 0.35;
          return <div key={`${p.t}-${i}`} style={{
            display: "grid", gridTemplateColumns: grid, alignItems: "center",
            padding: "0 8px", height: 16, borderBottom: `1px solid ${GRID}`,
          }}>
            <span style={{ fontSize: 9, color: DIMMER }}>{clock(p.t)}</span>
            <span style={{ fontSize: 10, color: tone, textAlign: "right", paddingRight: 8 }}>{p.p.toFixed(dp)}</span>
            <span style={{
              fontSize: 10, textAlign: "right", color: tone, fontWeight: heavy ? 700 : 500,
              background: `rgba(${rgb},${(0.05 + rel * 0.33).toFixed(3)})`,
              borderRadius: 2, padding: "1px 5px", marginRight: 6,
            }}>{qty(p.s)}</span>
            <span style={{ fontSize: 9, color: tone, textAlign: "right", letterSpacing: 0.5 }}>
              {p.side > 0 ? "BUY" : p.side < 0 ? "SELL" : "—"}
            </span>
          </div>;
        })}
    </div>

    <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 8px", borderTop: `1px solid ${BORDER}`, background: PANEL, fontSize: 9 }}>
      <span style={{ color: BUY }}>lifted {tape.length ? qty(stats.buyVol) : "—"}</span>
      <span style={{ color: DIMMER }}>largest print {tape.length ? qty(stats.maxSize) : "—"}</span>
      <span style={{ color: SELL }}>hit {tape.length ? qty(stats.sellVol) : "—"}</span>
    </div>
  </Shell>;
}
