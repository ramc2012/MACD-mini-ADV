import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DomLadder, TimeAndSales } from "./DomLadder";
import type { FootprintBar, FootprintData, FootprintProfile } from "./FootprintChart";
import { API_TOKEN, API_URL } from "./runtime";

/* Auction workstation: TPO profile, footprint and cumulative delta on one shared, scrollable time axis, across
   the stored sessions (completed days from /api/mp/session-footprint, today's from /api/mp/footprint), with the
   book, the tape and the key levels beside it.

   Interaction: drag = pan (time, and price in that pane) · wheel = zoom time around the cursor · shift+wheel =
   scroll time · drag or wheel on a price axis = price scale · drag the time axis = bar width · double-click =
   auto-fit · "Jump to live" returns to the newest bar. */

// Array.prototype.at ships in every browser this desk runs on (Chrome 92+, Safari 15.4+); the project's TS lib
// target predates it.
declare global {
  interface Array<T> { at(index: number): T | undefined }
  interface ReadonlyArray<T> { at(index: number): T | undefined }
}

const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
const TIMEFRAMES: [number, string][] = [[300, "5m"], [900, "15m"], [1800, "30m"], [3600, "1H"]];
const ROWS = [0, 0.5, 1, 2, 5, 10, 25, 50];
const HISTORY_SESSIONS = 5;
const POLL_MS = 3000;
const AXIS_W = 66;
const TIME_H = 22;
const HEAD_H = 18;
const STORE = "macd.workstation";
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const IST = "Asia/Kolkata";
const nf = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const qf = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const hm = new Intl.DateTimeFormat("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false });
const dayLabel = new Intl.DateTimeFormat("en-IN", { timeZone: IST, weekday: "short", day: "2-digit", month: "short" });
const istDay = (t = Date.now() / 1000) => new Date((t + 19_800) * 1000).toISOString().slice(0, 10);
const compact = (v: number) => {
  const a = Math.abs(v);
  return a >= 1e7 ? `${(v / 1e6).toFixed(1)}M` : a >= 1e4 ? `${(v / 1e3).toFixed(1)}K` : qf.format(Math.round(v));
};
const signed = (v: number) => `${v >= 0 ? "+" : ""}${compact(v)}`;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const C = {
  bg: "#0b0f17", grid: "#151c28", text: "#c9d4e3", dim: "#6f7d92", axis: "#9aa8bc", sep: "#2a3446",
  up: "#22c58b", down: "#f0566a", upFill: "rgba(34,197,139,0.20)", downFill: "rgba(240,86,106,0.20)",
  poc: "#f2c94c", va: "rgba(76,121,255,0.07)", vaLine: "#5b8cff", ib: "#a77bff", vwap: "#e86fb0",
  vp: "rgba(64,132,255,0.55)", cross: "rgba(180,195,215,0.45)", last: "#d64553",
};
const LETTER_COLORS = ["#7c5cff", "#9a6bff", "#3d7bd9", "#2f9bb3", "#2fa67a", "#7aa83a", "#c9a227", "#d97a2b",
  "#d1495b", "#b8487f", "#8a5bc4", "#4d6fd1", "#2e93a8"];

type Context = { prior_day?: { poc?: number; vah?: number; val?: number; high?: number; low?: number; close?: number } | null };
type Payload = FootprintData & { context?: Context | null; day?: string };
type Session = { day: string; live: boolean; data: Payload };
type Flat = { bar: FootprintBar; s: number; vwap: number | null; cvdOpen: number };
type PaneId = "tpo" | "fp" | "cvd";
type PaneView = { auto: boolean; center: number; ppp: number };
type Rect = { id: PaneId; top: number; h: number };

const recall = <T,>(fallback: T): T => {
  try { return { ...fallback, ...JSON.parse(window.localStorage.getItem(STORE) || "{}") }; } catch { return fallback; }
};
const remember = (value: unknown) => { try { window.localStorage.setItem(STORE, JSON.stringify(value)); } catch { /* private mode */ } };

function niceStep(raw: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

/** Letters of a profile level merged into the row bucket they fall in (period order: A..Z, then a..z). */
function letterRows(profile: FootprintProfile | null | undefined, row: number): Map<number, string> {
  const out = new Map<number, Set<string>>();
  for (const level of profile?.levels || []) {
    const key = Math.floor(level.price / row + 1e-9) * row;
    const set = out.get(key) || new Set<string>();
    for (const ch of level.letters || "") set.add(ch);
    out.set(key, set);
  }
  const order = (ch: string) => (ch >= "a" ? ch.charCodeAt(0) - 97 + 26 : ch.charCodeAt(0) - 65);
  return new Map([...out].map(([k, set]) => [k, [...set].sort((a, b) => order(a) - order(b)).join("")]));
}

export function AuctionWorkstation({ symbols }: { symbols: string[] }) {
  const saved = useMemo(() => recall({ symbol: "", tf: 1800, row: 0, tpo: true, fp: true, vp: true, cvd: true, vwap: true }), []);
  const [symbol, setSymbol] = useState(saved.symbol);
  const [tf, setTf] = useState<number>(saved.tf);
  const [row, setRow] = useState<number>(saved.row);
  const [show, setShow] = useState({ tpo: saved.tpo, fp: saved.fp, vp: saved.vp, cvd: saved.cvd, vwap: saved.vwap });
  useEffect(() => remember({ symbol, tf, row, ...show }), [symbol, tf, row, show]);
  // Futures first (front month leading), expired series dropped, then everything else alphabetically.
  const instruments = useMemo(() => {
    const now = new Date(Date.now() + 19_800_000);
    const current = (now.getUTCFullYear() % 100) * 12 + now.getUTCMonth();
    const month = (s: string) => {
      const m = /(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)FUT$/.exec(s);
      return m ? Number(m[1]) * 12 + MONTHS.indexOf(m[2]) : null;
    };
    const futures = symbols.filter((s) => { const m = month(s); return m != null && m >= current; })
      .sort((a, b) => (month(a)! - month(b)!) || a.localeCompare(b));
    return [...futures, ...symbols.filter((s) => month(s) == null)];
  }, [symbols]);
  useEffect(() => {
    if (!instruments.length || (symbol && instruments.includes(symbol))) return;
    setSymbol(instruments.find((s) => /^NSE:NIFTY\d{2}[A-Z]{3}FUT$/.test(s)) || instruments[0]);
  }, [instruments, symbol]);

  // ------------------------------------------------------------------------------------------------- data
  const [live, setLive] = useState<{ key: string; data: Payload; at: number; ms: number }>();
  const [liveError, setLiveError] = useState("");
  const history = useRef(new Map<string, Payload>());
  const [historyDays, setHistoryDays] = useState<string[]>([]);
  const [historyVersion, setHistoryVersion] = useState(0);
  const liveKey = `${symbol}|${tf}`;

  useEffect(() => {
    if (!symbol) return;
    let stop = false;
    const load = async () => {
      const started = performance.now();
      try {
        const response = await fetch(`${API_URL}/api/mp/footprint/${encodeURIComponent(symbol)}?bars=180&timeframe_seconds=${tf}&profile_full=true`, { headers });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json() as Payload;
        if (!stop) { setLive({ key: liveKey, data, at: Date.now(), ms: Math.round(performance.now() - started) }); setLiveError(""); }
      } catch (error) {
        if (!stop) setLiveError(error instanceof Error ? error.message : "live feed failed");
      }
    };
    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => { stop = true; window.clearInterval(timer); };
  }, [symbol, tf, liveKey]);

  useEffect(() => {
    if (!symbol) return;
    let stop = false;
    setHistoryDays([]);
    void (async () => {
      try {
        const response = await fetch(`${API_URL}/api/mp/replay-days/${encodeURIComponent(symbol)}`, { headers });
        const days = ((await response.json()) as string[]).filter((d) => d < istDay()).slice(0, HISTORY_SESSIONS).reverse();
        if (stop) return;
        setHistoryDays(days);
        // newest first: the session next to today is the one on screen when the chart opens
        for (const day of [...days].reverse()) {
          const key = `${symbol}|${tf}|${day}`;
          if (history.current.has(key)) continue;
          const r = await fetch(`${API_URL}/api/mp/session-footprint/${encodeURIComponent(symbol)}?day=${day}&timeframe_seconds=${tf}`, { headers });
          if (stop) return;
          if (r.ok) { history.current.set(key, await r.json() as Payload); setHistoryVersion((v) => v + 1); }
        }
      } catch { /* history is a convenience: the live session still draws */ }
    })();
    return () => { stop = true; };
  }, [symbol, tf]);

  const sessions = useMemo<Session[]>(() => {
    const out: Session[] = [];
    for (const day of historyDays) {
      const data = history.current.get(`${symbol}|${tf}|${day}`);
      if (data?.bars?.length) out.push({ day, live: false, data });
    }
    if (live?.key === liveKey && live.data.bars?.length) out.push({ day: istDay(), live: true, data: live.data });
    return out;
    // historyVersion: the Map is mutated in place
  }, [historyDays, symbol, tf, live, liveKey, historyVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  const tick = sessions.at(-1)?.data.tick_size || 0.05;
  const flat = useMemo<Flat[]>(() => {
    const out: Flat[] = [];
    sessions.forEach((session, s) => {
      let pv = 0, vol = 0, prevCvd = 0;
      for (const bar of [...session.data.bars].sort((a, b) => a.t - b.t)) {
        for (const level of bar.levels || []) {
          const v = (level.bid || 0) + (level.ask || 0) + (level.u || 0);
          pv += level.p * v; vol += v;
        }
        if (!(bar.levels || []).length && bar.v) { pv += bar.c * bar.v; vol += bar.v; }
        out.push({ bar, s, vwap: vol ? pv / vol : null, cvdOpen: prevCvd });
        prevCvd = bar.cvd;
      }
    });
    return out;
  }, [sessions]);

  // --------------------------------------------------------------------------------------------- view state
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const view = useRef({ barW: 120, offset: 0, panes: { tpo: { auto: true, center: 0, ppp: 1 }, fp: { auto: true, center: 0, ppp: 1 },
                                                          cvd: { auto: true, center: 0, ppp: 1 } } as Record<PaneId, PaneView> });
  const mouse = useRef<{ x: number; y: number } | null>(null);
  const layoutRef = useRef<{ rects: Rect[]; plotW: number; plotH: number; rowSize: number }>({ rects: [], plotW: 0, plotH: 0, rowSize: 1 });
  const [awayFromLive, setAwayFromLive] = useState(false);
  const [renderMs, setRenderMs] = useState(0);
  const [hover, setHover] = useState<Flat | null>(null);
  const frame = useRef(0);
  const hoverRef = useRef<Flat | null>(null);
  const lastStat = useRef(0);

  const draw = useCallback(() => {
    const el = canvas.current;
    const box = wrap.current;
    if (!el || !box) return;
    const started = performance.now();
    const dpr = window.devicePixelRatio || 1;
    const W = box.clientWidth, H = box.clientHeight;
    if (el.width !== Math.round(W * dpr) || el.height !== Math.round(H * dpr)) {
      el.width = Math.round(W * dpr); el.height = Math.round(H * dpr);
      el.style.width = `${W}px`; el.style.height = `${H}px`;
    }
    const g = el.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = C.bg; g.fillRect(0, 0, W, H);
    const v = view.current;
    const plotW = W - AXIS_W, plotH = H - TIME_H;
    const panes: [PaneId, number][] = ([["tpo", 0.36], ["fp", 0.46], ["cvd", 0.18]] as [PaneId, number][])
      .filter(([id]) => (id === "tpo" ? show.tpo : id === "fp" ? show.fp : show.cvd));
    const total = panes.reduce((a, [, w]) => a + w, 0) || 1;
    let y0 = 0;
    const rects: Rect[] = panes.map(([id, w]) => { const r = { id, top: y0, h: Math.round(plotH * w / total) }; y0 += r.h; return r; });
    const N = flat.length;
    const barW = v.barW;
    // Volumes in lots for derivatives (as a trader reads the tape), in shares for cash equities.
    const unit = sessions.at(-1)?.data.lot_size || 1;
    const q = (x: number) => compact(x / unit);
    v.offset = Math.max(-Math.floor(plotW / barW * 0.5), Math.min(Math.max(0, N - 1), v.offset));
    // offset = how many bars the right edge sits behind the newest one (0 = live)
    const xOf = (i: number) => plotW - (N - 1 - v.offset - i) * barW - barW / 2;
    const iFirst = Math.max(0, Math.floor(N - 1 - v.offset - plotW / barW) - 1);
    const iLast = Math.min(N - 1, Math.ceil(N - 1 - v.offset) + 1);
    const visible = N ? flat.slice(iFirst, iLast + 1) : [];

    // row size: chosen, or the nicest step that gives the footprint ~14 px rows over the visible range
    const fpRect = rects.find((r) => r.id === "fp");
    let lo = Infinity, hi = -Infinity;
    for (const f of visible) { lo = Math.min(lo, f.bar.l); hi = Math.max(hi, f.bar.h); }
    const span = hi > lo ? hi - lo : tick * 20;
    const rowSize = row > 0 ? row : Math.max(tick, niceStep(span / Math.max(8, ((fpRect?.h || 300) - HEAD_H) / 14)));
    layoutRef.current = { rects, plotW, plotH, rowSize };

    const fit = (pane: PaneView, min: number, max: number, h: number) => {
      if (!pane.auto || !(max > min)) return;
      const pad = (max - min) * 0.08;
      pane.center = (min + max) / 2;
      pane.ppp = (h - HEAD_H - 14) / (max - min + 2 * pad);
    };

    // ---------- pane bodies
    for (const rect of rects) {
      const pane = v.panes[rect.id];
      const top = rect.top + HEAD_H, h = rect.h - HEAD_H;
      if (rect.id === "fp") fit(pane, lo, hi, rect.h);
      if (rect.id === "tpo") {
        let a = Infinity, b = -Infinity;
        const seen = new Set(visible.map((f) => f.s));
        for (const s of seen) { const p = sessions[s].data.profile; if (p?.low != null && p?.high != null) { a = Math.min(a, p.low); b = Math.max(b, p.high); } }
        fit(pane, a, b, rect.h);
      }
      if (rect.id === "cvd") {
        let a = Infinity, b = -Infinity;
        for (const f of visible) { a = Math.min(a, f.cvdOpen, f.bar.cvd); b = Math.max(b, f.cvdOpen, f.bar.cvd); }
        fit(pane, a, b, rect.h);
      }
      const yOf = (price: number) => top + h / 2 - (price - pane.center) * pane.ppp;
      g.save();
      g.beginPath(); g.rect(0, top, plotW, h); g.clip();
      // grid + price axis
      const stepP = niceStep(40 / Math.max(pane.ppp, 1e-9));
      const pLo = pane.center - h / 2 / pane.ppp, pHi = pane.center + h / 2 / pane.ppp;
      g.strokeStyle = C.grid; g.lineWidth = 1;
      for (let p = Math.ceil(pLo / stepP) * stepP; p <= pHi; p += stepP) {
        const y = Math.round(yOf(p)) + 0.5; g.beginPath(); g.moveTo(0, y); g.lineTo(plotW, y); g.stroke();
      }
      if (rect.id === "tpo") drawTpo(g, rect, pane, yOf, plotW);
      if (rect.id === "fp") drawFootprint(g, yOf, top, h, plotW);
      if (rect.id === "cvd") drawCvd(g, yOf);
      g.restore();
      // axis labels
      g.fillStyle = C.bg; g.fillRect(plotW, rect.top, AXIS_W, rect.h);
      g.fillStyle = C.axis; g.font = "10px ui-monospace, Menlo, monospace"; g.textAlign = "left"; g.textBaseline = "middle";
      for (let p = Math.ceil(pLo / stepP) * stepP; p <= pHi; p += stepP) {
        const y = yOf(p);
        if (y > top + 6 && y < top + h - 6) g.fillText(rect.id === "cvd" ? compact(p / unit) : nf.format(p), plotW + 6, y);
      }
      const last = sessions.at(-1)?.data.bars.at(-1)?.c;
      if (finite(last) && rect.id !== "cvd") {
        const y = yOf(last);
        if (y > top && y < top + h) {
          g.fillStyle = C.last; g.fillRect(plotW, y - 8, AXIS_W, 16);
          g.fillStyle = "#fff"; g.fillText(nf.format(last), plotW + 6, y);
        }
      }
      // pane separator + title
      g.strokeStyle = C.sep; g.beginPath(); g.moveTo(0, rect.top + 0.5); g.lineTo(W, rect.top + 0.5); g.stroke();
      g.fillStyle = C.bg; g.fillRect(0, rect.top + 1, plotW, HEAD_H - 1);
      g.textBaseline = "middle"; g.font = "600 11px Inter, sans-serif"; g.fillStyle = C.text;
      const title = rect.id === "tpo" ? "Market Profile (TPO)" : rect.id === "fp" ? `Footprint ${TIMEFRAMES.find((t) => t[0] === tf)?.[1] || ""}` : "Cumulative Delta";
      g.fillText(title, 8, rect.top + HEAD_H / 2);
      const tw = g.measureText(title).width;
      g.font = "10px ui-monospace, Menlo, monospace"; g.fillStyle = C.dim;
      const note = rect.id === "tpo" ? `30m letters · row ${rowSize} · VA 70% · IB 2 periods`
        : rect.id === "cvd" ? `session CVD ${signed((sessions.at(-1)?.data.session_cumulative_delta ?? visible.at(-1)?.bar.cvd ?? 0) / unit)}${unit > 1 ? " lots" : ""}`
        : hoverNote();
      g.fillText(note, 16 + tw, rect.top + HEAD_H / 2);
    }

    // ---------- session separators and the time axis
    g.font = "10px ui-monospace, Menlo, monospace"; g.textBaseline = "middle"; g.textAlign = "center";
    g.fillStyle = C.bg; g.fillRect(0, plotH, W, TIME_H);
    g.strokeStyle = C.sep; g.beginPath(); g.moveTo(0, plotH + 0.5); g.lineTo(W, plotH + 0.5); g.stroke();
    const labelEvery = Math.max(1, Math.ceil(70 / barW));
    for (let i = iFirst; i <= iLast; i++) {
      const f = flat[i]; if (!f) continue;
      const x = xOf(i);
      const first = i === 0 || flat[i - 1].s !== f.s;
      if (first && i > 0) {
        g.save(); g.setLineDash([4, 4]); g.strokeStyle = "#3a4a63";
        g.beginPath(); g.moveTo(Math.round(x - barW / 2) + 0.5, 0); g.lineTo(Math.round(x - barW / 2) + 0.5, plotH); g.stroke(); g.restore();
      }
      if (first) {
        g.fillStyle = C.text; g.fillText(dayLabel.format(new Date(f.bar.t * 1000)), x, plotH + TIME_H / 2);
      } else if (i % labelEvery === 0) {
        g.fillStyle = C.dim; g.fillText(hm.format(new Date(f.bar.t * 1000)), x, plotH + TIME_H / 2);
      }
    }

    // ---------- crosshair
    const m = mouse.current;
    if (m && m.x < plotW && m.y < plotH) {
      g.strokeStyle = C.cross; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(Math.round(m.x) + 0.5, 0); g.lineTo(Math.round(m.x) + 0.5, plotH); g.stroke();
      const rect = rects.find((r) => m.y >= r.top + HEAD_H && m.y < r.top + r.h);
      if (rect) {
        g.beginPath(); g.moveTo(0, Math.round(m.y) + 0.5); g.lineTo(plotW, Math.round(m.y) + 0.5); g.stroke();
        const pane = v.panes[rect.id];
        const price = pane.center + (rect.top + HEAD_H + (rect.h - HEAD_H) / 2 - m.y) / pane.ppp;
        g.setLineDash([]); g.fillStyle = "#2b3a52"; g.fillRect(plotW, m.y - 8, AXIS_W, 16);
        g.fillStyle = "#fff"; g.textAlign = "left"; g.fillText(rect.id === "cvd" ? compact(price / unit) : nf.format(price), plotW + 6, m.y);
      }
      g.setLineDash([]);
      const i = Math.round(N - 1 - v.offset - (plotW - barW / 2 - m.x) / barW);
      const f = flat[i];
      if (f) {
        const label = `${dayLabel.format(new Date(f.bar.t * 1000))} ${hm.format(new Date(f.bar.t * 1000))}`;
        g.font = "10px ui-monospace, Menlo, monospace";
        const w = g.measureText(label).width + 12;
        g.fillStyle = "#2b3a52"; g.fillRect(m.x - w / 2, plotH + 2, w, TIME_H - 4);
        g.fillStyle = "#fff"; g.textAlign = "center"; g.fillText(label, m.x, plotH + TIME_H / 2);
      }
    }
    if (!N) {
      g.fillStyle = C.dim; g.font = "12px Inter, sans-serif"; g.textAlign = "center";
      g.fillText(symbol ? "Waiting for footprint bars (live from the open; past sessions while raw ticks are on disk)" : "Pick an instrument", plotW / 2, plotH / 2);
    }
    const away = v.offset > 0.5;
    setAwayFromLive((old) => (old === away ? old : away));
    // a readout, not a meter: refreshed twice a second so mouse moves do not re-render the side panels each frame
    if (performance.now() - lastStat.current > 500) {
      lastStat.current = performance.now();
      setRenderMs(Math.round((performance.now() - started) * 10) / 10);
    }

    // -------------------------------------------------------------------------------------- pane painters
    function hoverNote(): string {
      const f = hoverRef.current;
      if (!f) return "";
      const b = f.bar;
      return `${dayLabel.format(new Date(b.t * 1000))} ${hm.format(new Date(b.t * 1000))}   O ${nf.format(b.o)}  H ${nf.format(b.h)}  L ${nf.format(b.l)}  C ${nf.format(b.c)}   V ${q(b.v)}   Δ ${signed(b.delta / unit)}${unit > 1 ? " lots" : ""}`;
    }

    function drawTpo(gc: CanvasRenderingContext2D, rect: Rect, pane: PaneView, yOf: (p: number) => number, pw: number) {
      const rowH = Math.max(1, rowSize * pane.ppp);
      const sessionsOnScreen = [...new Set(visible.map((f) => f.s))];
      for (const s of sessionsOnScreen) {
        const profile = sessions[s].data.profile;
        if (!profile) continue;
        const idx = flat.map((f, i) => (f.s === s ? i : -1)).filter((i) => i >= 0);
        const x0 = xOf(idx[0]) - barW / 2, x1 = xOf(idx[idx.length - 1]) + barW / 2;
        if (x1 < 0 || x0 > pw) continue;
        const rows = letterRows(profile, rowSize);
        const cw = Math.max(5, Math.min(9, (x1 - x0 - 120) / 26));
        if (finite(profile.vah) && finite(profile.val)) {
          gc.fillStyle = C.va; gc.fillRect(x0, yOf(profile.vah + rowSize), x1 - x0, yOf(profile.val) - yOf(profile.vah + rowSize));
        }
        gc.font = `${Math.min(10, Math.max(7, rowH - 2))}px ui-monospace, Menlo, monospace`;
        gc.textAlign = "center"; gc.textBaseline = "middle";
        for (const [price, letters] of rows) {
          const yT = yOf(price + rowSize), hRow = Math.max(1, yOf(price) - yT - 1);
          if (yT > rect.top + rect.h || yT + hRow < rect.top + HEAD_H) continue;
          for (let k = 0; k < letters.length; k++) {
            const ch = letters[k];
            const period = ch >= "a" ? ch.charCodeAt(0) - 97 + 26 : ch.charCodeAt(0) - 65;
            const x = Math.max(x0, 0) + 6 + k * cw;      // anchored where the session is visible
            if (x > x1 - 2) break;
            gc.fillStyle = period < 2 ? C.ib : LETTER_COLORS[period % LETTER_COLORS.length];
            gc.globalAlpha = 0.75; gc.fillRect(x, yT, cw - 1, hRow); gc.globalAlpha = 1;
            if (hRow >= 7 && cw >= 6) { gc.fillStyle = "#e9edf5"; gc.fillText(ch, x + (cw - 1) / 2, yT + hRow / 2); }
          }
        }
        const line = (price: number | null | undefined, color: string, label: string, dash: number[]) => {
          if (!finite(price)) return;
          const y = Math.round(yOf(price)) + 0.5;
          gc.setLineDash(dash); gc.strokeStyle = color; gc.beginPath(); gc.moveTo(x0, y); gc.lineTo(x1, y); gc.stroke(); gc.setLineDash([]);
          gc.font = "10px ui-monospace, Menlo, monospace"; gc.textAlign = "right"; gc.fillStyle = color;
          gc.fillText(`${label} ${nf.format(price)}`, Math.min(x1, pw) - 6, y - 7);
        };
        line(profile.poc, C.poc, "POC", []);
        line(profile.vah, C.vaLine, "VAH", [4, 3]);
        line(profile.val, C.vaLine, "VAL", [4, 3]);
        line(profile.ib_high, C.ib, "IBH", [2, 3]);
        line(profile.ib_low, C.ib, "IBL", [2, 3]);
      }
    }

    function drawFootprint(gc: CanvasRenderingContext2D, yOf: (p: number) => number, top: number, h: number, pw: number) {
      const pane = v.panes.fp;
      const rowH = rowSize * pane.ppp;
      const showText = rowH >= 11 && barW >= 70;
      const ratio = sessions.at(-1)?.data.imbalance_ratio || 3;
      gc.textBaseline = "middle";
      for (let i = iFirst; i <= iLast; i++) {
        const f = flat[i]; if (!f) continue;
        const b = f.bar, xc = xOf(i), xL = xc - barW / 2 + 2, xR = xc + barW / 2 - 2;
        // candle on the left edge of the slot
        const up = b.c >= b.o;
        gc.strokeStyle = up ? C.up : C.down; gc.fillStyle = up ? C.up : C.down;
        gc.beginPath(); gc.moveTo(xL + 2.5, yOf(b.h)); gc.lineTo(xL + 2.5, yOf(b.l)); gc.stroke();
        gc.fillRect(xL + 1, Math.min(yOf(b.o), yOf(b.c)), 3, Math.max(1, Math.abs(yOf(b.o) - yOf(b.c))));
        // cells
        const cells = new Map<number, { bid: number; ask: number }>();
        for (const level of b.levels || []) {
          const key = Math.floor(level.p / rowSize + 1e-9) * rowSize;
          const cell = cells.get(key) || { bid: 0, ask: 0 };
          cell.bid += level.bid || 0; cell.ask += level.ask || 0;
          cells.set(key, cell);
        }
        let pocKey = NaN, pocVol = -1, maxVol = 1;
        for (const [k, c] of cells) { const t = c.bid + c.ask; if (t > pocVol) { pocVol = t; pocKey = k; } maxVol = Math.max(maxVol, t); }
        const cx0 = xL + 7, cw = Math.max(1, xR - cx0);
        gc.font = "10px ui-monospace, Menlo, monospace";
        for (const [k, c] of cells) {
          const yT = yOf(k + rowSize), hh = Math.max(1, yOf(k) - yT - (showText ? 1 : 0));
          if (yT > top + h || yT + hh < top) continue;
          const buyImb = c.ask > 0 && c.ask >= ratio * Math.max(c.bid, 1);
          const sellImb = c.bid > 0 && c.bid >= ratio * Math.max(c.ask, 1);
          const net = c.ask - c.bid;
          gc.fillStyle = buyImb ? "rgba(34,197,139,0.42)" : sellImb ? "rgba(240,86,106,0.42)"
            : net >= 0 ? `rgba(34,197,139,${0.08 + 0.3 * (c.bid + c.ask) / maxVol})` : `rgba(240,86,106,${0.08 + 0.3 * (c.bid + c.ask) / maxVol})`;
          gc.fillRect(cx0, yT, cw, hh);
          if (k === pocKey) { gc.strokeStyle = C.poc; gc.strokeRect(cx0 + 0.5, yT + 0.5, cw - 1, hh - 1); }
          if (showText) {
            gc.textAlign = "right"; gc.fillStyle = sellImb ? "#ff8a98" : "#d9e1ec"; gc.fillText(q(c.bid), cx0 + cw / 2 - 6, yT + hh / 2);
            gc.textAlign = "center"; gc.fillStyle = C.dim; gc.fillText("x", cx0 + cw / 2, yT + hh / 2);
            gc.textAlign = "left"; gc.fillStyle = buyImb ? "#5ff0b8" : "#d9e1ec"; gc.fillText(q(c.ask), cx0 + cw / 2 + 6, yT + hh / 2);
          }
        }
        if (barW >= 50) {
          const yB = yOf(b.l) + 12;
          gc.textAlign = "center"; gc.font = "10px ui-monospace, Menlo, monospace";
          gc.fillStyle = b.delta >= 0 ? C.up : C.down; gc.fillText(`Δ ${signed(b.delta / unit)}`, xc, yB);
          gc.fillStyle = C.dim; gc.fillText(`V ${q(b.v)}`, xc, yB + 12);
        }
      }
      // VWAP, per session
      if (show.vwap) {
        gc.strokeStyle = C.vwap; gc.lineWidth = 1.5; gc.beginPath();
        let open = false, lastS = -1;
        for (let i = iFirst; i <= iLast; i++) {
          const f = flat[i]; if (!f || f.vwap == null) { open = false; continue; }
          const x = xOf(i), y = yOf(f.vwap);
          if (!open || f.s !== lastS) gc.moveTo(x, y); else gc.lineTo(x, y);
          open = true; lastS = f.s;
        }
        gc.stroke(); gc.lineWidth = 1;
      }
      // visible-range volume profile on the right edge
      if (show.vp && visible.length) {
        const vp = new Map<number, number>();
        for (const f of visible) for (const level of f.bar.levels || []) {
          const key = Math.floor(level.p / rowSize + 1e-9) * rowSize;
          vp.set(key, (vp.get(key) || 0) + (level.bid || 0) + (level.ask || 0));
        }
        let max = 1, pocK = NaN;
        for (const [k, val] of vp) if (val > max) { max = val; pocK = k; }
        const width = Math.min(150, pw * 0.18);
        for (const [k, val] of vp) {
          const yT = yOf(k + rowSize), hh = Math.max(1, yOf(k) - yT - 1);
          gc.fillStyle = k === pocK ? C.poc : C.vp;
          gc.globalAlpha = k === pocK ? 0.85 : 1;
          gc.fillRect(pw - width * val / max, yT, width * val / max, hh);
          gc.globalAlpha = 1;
        }
        gc.font = "10px ui-monospace, Menlo, monospace"; gc.textAlign = "right"; gc.fillStyle = C.dim;
        gc.fillText("VP (visible)", pw - 6, top + 10);
      }
    }

    function drawCvd(gc: CanvasRenderingContext2D, yOf: (p: number) => number) {
      const zero = yOf(0);
      gc.strokeStyle = C.sep; gc.beginPath(); gc.moveTo(0, zero); gc.lineTo(plotW, zero); gc.stroke();
      for (let i = iFirst; i <= iLast; i++) {
        const f = flat[i]; if (!f) continue;
        const x = xOf(i), up = f.bar.cvd >= f.cvdOpen;
        const y1 = yOf(f.cvdOpen), y2 = yOf(f.bar.cvd);
        gc.fillStyle = up ? C.up : C.down;
        gc.fillRect(x - Math.max(1, barW * 0.38), Math.min(y1, y2), Math.max(2, barW * 0.76), Math.max(2, Math.abs(y2 - y1)));
      }
    }
  }, [flat, sessions, show, row, tick, tf, symbol]);

  const schedule = useCallback(() => {
    if (frame.current) return;
    frame.current = window.requestAnimationFrame(() => { frame.current = 0; draw(); });
  }, [draw]);
  useEffect(() => { schedule(); }, [schedule]);
  useEffect(() => {
    const box = wrap.current; if (!box) return;
    const observer = new ResizeObserver(() => schedule());
    observer.observe(box);
    return () => observer.disconnect();
  }, [schedule]);

  // ------------------------------------------------------------------------------------------ interaction
  const drag = useRef<null | { kind: "plot" | "price" | "time"; pane?: PaneId; x: number; y: number; offset: number; barW: number; center: number; ppp: number }>(null);
  const paneAt = (y: number) => layoutRef.current.rects.find((r) => y >= r.top && y < r.top + r.h);
  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { offsetX: x, offsetY: y } = event.nativeEvent;
    const { plotW, plotH } = layoutRef.current;
    const pane = paneAt(y);
    const pv = pane ? view.current.panes[pane.id] : undefined;
    const kind = y >= plotH ? "time" : x >= plotW ? "price" : "plot";
    drag.current = { kind, pane: pane?.id, x, y, offset: view.current.offset, barW: view.current.barW, center: pv?.center ?? 0, ppp: pv?.ppp ?? 1 };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { offsetX: x, offsetY: y } = event.nativeEvent;
    mouse.current = { x, y };
    const d = drag.current;
    const v = view.current;
    if (d) {
      const dx = x - d.x, dy = y - d.y;
      if (d.kind === "plot") {
        v.offset = d.offset + dx / v.barW;          // drag right = pull older bars into view
        if (d.pane && Math.abs(dy) > 2) { const p = v.panes[d.pane]; p.auto = false; p.center = d.center + dy / p.ppp; }
      } else if (d.kind === "price" && d.pane) {
        const p = v.panes[d.pane]; p.auto = false; p.center = d.center; p.ppp = Math.max(1e-6, d.ppp * Math.exp(-dy / 150));
      } else if (d.kind === "time") {
        v.barW = Math.max(4, Math.min(320, d.barW * Math.exp(dx / 200)));
      }
    }
    const { plotW } = layoutRef.current;
    const N = flat.length;
    const i = Math.round(N - 1 - v.offset - (plotW - v.barW / 2 - x) / v.barW);
    const f = flat[i] || null;
    if (f !== hoverRef.current) { hoverRef.current = f; setHover(f); }
    schedule();
  };
  const onPointerUp = () => { drag.current = null; };
  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    const { offsetX: x, offsetY: y } = event.nativeEvent;
    const v = view.current;
    const { plotW } = layoutRef.current;
    const delta = event.deltaY || event.deltaX;
    if (x >= plotW) {
      const pane = paneAt(y);
      if (pane) { const p = v.panes[pane.id]; p.auto = false; p.ppp = Math.max(1e-6, p.ppp * Math.exp(-delta / 400)); }
    } else if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      v.offset += delta / v.barW;
    } else {
      // zoom around the bar under the cursor
      const N = flat.length;
      const iCursor = N - 1 - v.offset - (plotW - v.barW / 2 - x) / v.barW;
      v.barW = Math.max(4, Math.min(320, v.barW * Math.exp(-delta / 500)));
      v.offset = N - 1 - iCursor - (plotW - v.barW / 2 - x) / v.barW;
    }
    schedule();
  };
  useEffect(() => {
    // React's onWheel is passive: stop the page from scrolling under the chart
    const el = canvas.current; if (!el) return;
    const block = (event: WheelEvent) => event.preventDefault();
    el.addEventListener("wheel", block, { passive: false });
    return () => el.removeEventListener("wheel", block);
  }, []);
  const autoFit = () => { for (const p of Object.values(view.current.panes)) p.auto = true; schedule(); };
  const jumpLive = () => { view.current.offset = 0; autoFit(); };

  // ----------------------------------------------------------------------------------------- side panels
  const current = sessions.at(-1);
  const data = current?.data;
  const lastBar = data?.bars.at(-1);
  const prior = data?.context?.prior_day || sessions.at(-2)?.data.profile && {
    poc: sessions.at(-2)!.data.profile!.poc, vah: sessions.at(-2)!.data.profile!.vah, val: sessions.at(-2)!.data.profile!.val,
    high: sessions.at(-2)!.data.profile!.high, low: sessions.at(-2)!.data.profile!.low, close: sessions.at(-2)!.data.bars.at(-1)?.c,
  } || null;
  const sessionFlat = flat.filter((f) => f.s === sessions.length - 1);
  const vwapNow = sessionFlat.at(-1)?.vwap ?? null;
  const change = lastBar && finite(prior?.close) ? lastBar.c - prior!.close! : null;
  const levels: [string, number | null | undefined, string][] = [
    ["POC", data?.profile?.poc, C.poc], ["VAH", data?.profile?.vah, C.vaLine], ["VAL", data?.profile?.val, C.vaLine],
    ["IB High", data?.profile?.ib_high, C.ib], ["IB Low", data?.profile?.ib_low, C.ib], ["VWAP", vwapNow, C.vwap],
    ["Session High", data?.profile?.high, C.text], ["Session Low", data?.profile?.low, C.text],
    ["PD High", prior?.high, C.dim], ["PD Low", prior?.low, C.dim], ["PD Close", prior?.close, C.dim],
    ["PD POC", prior?.poc, C.poc], ["PD VAH", prior?.vah, C.vaLine], ["PD VAL", prior?.val, C.vaLine],
  ];
  const connected = !!live && Date.now() - live.at < 15_000 && !liveError;
  const sessionVol = (current?.live ? current.data.bars : ([] as FootprintBar[])).reduce((a: number, b: FootprintBar) => a + (b.v || 0), 0);

  return <div className="ws-root">
    <div className="ws-toolbar">
      <select className="ws-symbol" value={symbol} onChange={(e) => { setSymbol(e.target.value); view.current.offset = 0; autoFit(); }} aria-label="Instrument">
        {instruments.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <span className="ws-last">{lastBar ? nf.format(lastBar.c) : "—"}
        {change != null && <em className={change >= 0 ? "positive" : "negative"}>{change >= 0 ? "+" : ""}{nf.format(change)} ({((change / (prior?.close || 1)) * 100).toFixed(2)}%)</em>}</span>
      <div className="ws-group">{TIMEFRAMES.map(([value, label]) =>
        <button key={value} className={tf === value ? "active" : ""} onClick={() => setTf(value)}>{label}</button>)}</div>
      <div className="ws-group">
        {([["tpo", "TPO"], ["fp", "Footprint"], ["vp", "Volume Profile"], ["cvd", "CVD"]] as [keyof typeof show, string][]).map(([key, label]) =>
          <button key={key} className={show[key] ? "active" : ""} onClick={() => setShow((old) => ({ ...old, [key]: !old[key] }))}>{label}</button>)}
      </div>
      <button className={`ws-toggle ${show.vwap ? "active" : ""}`} onClick={() => setShow((old) => ({ ...old, vwap: !old.vwap }))}>VWAP</button>
      <label className="ws-row">Row <select value={row} onChange={(e) => setRow(Number(e.target.value))}>
        {ROWS.map((r) => <option key={r} value={r}>{r === 0 ? "auto" : r}</option>)}</select> pts</label>
      <button className="ws-toggle" onClick={autoFit} title="Auto-fit every pane (or double-click the chart)">Fit</button>
      <span className="ws-help" title="drag = pan · wheel = zoom · shift+wheel = scroll · drag price axis = scale · drag time axis = bar width · double-click = auto-fit">?</span>
    </div>

    <div className="ws-main">
      <div className="ws-chart" ref={wrap}>
        <canvas ref={canvas} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
                onPointerLeave={() => { mouse.current = null; schedule(); }} onWheel={onWheel} onDoubleClick={autoFit}
                aria-label="TPO, footprint and cumulative delta chart" />
        {awayFromLive && <button className="ws-jump" onClick={jumpLive}>⇥ Jump to live</button>}
      </div>
      <aside className="ws-side">
        {/* Fyers streams the touch only: sizes at the best bid/ask, traded volume at every price */}
        <section className="ws-panel ws-bare">
          <DomLadder dom={current?.live ? data?.dom || null : null} profile={data?.profile || null} tickSize={tick} lastPrice={lastBar?.c ?? null} />
        </section>
        <section className="ws-panel ws-bare">
          <TimeAndSales tape={(current?.live ? data?.tape : []) || []} tickSize={tick} height={180} />
        </section>
        <section className="ws-panel ws-levels">
          <header>Key Levels <small>row {layoutRef.current.rowSize || "auto"} pts</small></header>
          {levels.map(([name, value, color]) => <div key={name}><i style={{ background: color }} /><span>{name}</span>
            <b style={{ color }}>{finite(value) ? nf.format(value) : "—"}</b></div>)}
        </section>
      </aside>
    </div>

    <footer className="ws-status">
      <span className={connected ? "ok" : "bad"}>● {connected ? "Connected" : liveError || "Waiting"}</span>
      <span>Latency {live ? `${live.ms} ms` : "—"}</span>
      <span>Session 09:15–15:30 IST</span>
      <span>Vol {compact(sessionVol / (data?.lot_size || 1))}{data?.lot_size ? " lots" : ""}</span>
      <span className={(data?.session_cumulative_delta ?? 0) >= 0 ? "positive" : "negative"}>Δ session {signed((current?.live ? data?.session_cumulative_delta ?? 0 : 0) / (data?.lot_size || 1))}</span>
      <span>History {sessions.filter((s) => !s.live).length} sessions</span>
      <span className="ws-spacer" />
      {hover && <span>{hm.format(new Date(hover.bar.t * 1000))} · V {qf.format(hover.bar.v)} · Δ {signed(hover.bar.delta)}</span>}
      <span>{renderMs} ms render · {flat.length} bars</span>
      <span>Tick {tick} · Row {layoutRef.current.rowSize} · {TIMEFRAMES.find((t) => t[0] === tf)?.[1]}</span>
    </footer>
  </div>;
}
