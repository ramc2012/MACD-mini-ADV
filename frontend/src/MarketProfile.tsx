import { AreaSeries, ColorType, createChart, LineSeries, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { OrderFlowWorkspace, SessionBadges } from "./OrderFlowWorkspace";
import { MarketProfileLadder, type LadderProfile } from "./MarketProfileLadder";
import type { SessionInfo } from "./types";
import { API_TOKEN, API_URL } from "./runtime";

const money = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const clock = (v: string) => new Date(v).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const stamp = (v: string) => new Date(v).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const cvdMinute = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const cvdStamp = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const formatCvdTime = (time: Time, full = false) => typeof time === "number"
  ? (full ? cvdStamp : cvdMinute).format(new Date(time * 1000)) : String(time);
const short = (s: string) => s.split(":")[1] || s;
const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
const words = (s?: string | null) => (s ? s.replace(/_/g, " ") : "—");
const pct = (v?: number | null) => (v == null ? "—" : `${v.toFixed(2)}%`);

type ProfileData = LadderProfile & {
  symbol: string; day: string; open: number | null; last: number | null; high: number | null; low: number | null;
  poc: number | null; vah: number | null; val: number | null; ib_high: number | null; ib_low: number | null;
  brackets: number; open_type: string; day_type: string; position: string;
  open_observed?: boolean; ib_complete?: boolean; first_bracket?: number | null;
};
type Flow = {
  symbol: string; trades: number; buy_volume: number; sell_volume: number; delta: number; cumulative_delta: number;
  imbalance: number; absorption: { detected: boolean; side?: string | null; pressure?: number; range_pct?: number };
  divergence: { detected: boolean; kind?: string | null };
  footprint: { price: number; buy: number; sell: number; delta: number }[];
  cvd_curve: { t: number; cvd: number; price: number }[];
};
// MFE/MAE since entry, from the desk's in-session marks. Optional because
// the fields are empty between a restart and the first restore_state.
type Excursion = { max_price?: number | null; min_price?: number | null; max_return_pct?: number | null; min_return_pct?: number | null; max_at?: string | null; min_at?: string | null };
type Position = Excursion & { symbol: string; lots: number; lot_size: number; quantity: number; average_price: number; last_price: number; unrealized_pnl: number; opened_at: string; hard_stop?: number; trailing_stop?: number | null; peak_price?: number };
type ClosedPosition = Excursion & {
  id: string; order_id: string; symbol: string; setup: string | null; option_type: string; underlying: string | null;
  entry_time: string; entry_price: number; exit_time: string; exit_price: number; exit_reason: string;
  quantity: number; lots: number; lot_size: number; pnl: number; return_pct: number; fees: number;
  partial: boolean; closed_day: string; visible_until: string;
};
type Row = Record<string, string | number | null>;
type Snap = {
  enabled: boolean; settings: Record<string, number | boolean>; session_day: string | null; prints_seen: number;
  trades_today: number; tracked_symbols: string[]; subscribed_symbols?: string[]; subscribed_count?: number; focus: string | null; profile: ProfileData | null; flow: Flow | null;
  setup: { setup: string | null; reason: string } | null;
  portfolio: { equity: number; cash: number; realized_pnl: number; unrealized_pnl: number; positions: Position[] };
  closed_positions?: ClosedPosition[];
  closed_positions_retention?: { until_hour_ist: number };
  entry_state: Record<string, { setup?: string; entered_at?: string; exit_reason?: string; carry?: { allowed: boolean; reason: string; evaluated_at: string } }>;
  classification?: { quote_share: number; unclassified_share: number; prints: number };
  // The badge strip for the focused symbol; null before its first print.
  session?: SessionInfo | null;
  health?: {
    mode: "paper"; session_open: boolean; ready_for_auto_paper: boolean; blockers: string[];
    last_tick_age_seconds: number | null; errors_total: number;
    persistence: { saved_at: string | null; bytes: number; error: string | null };
  };
};
type Leader = {
  symbol: string; last: number; open: number | null; high: number | null; low: number | null; poc: number | null;
  vah: number | null; val: number | null; ib_high: number | null; ib_low: number | null; brackets: number;
  position: string; day_type: string; open_type: string; imbalance: number; cumulative_delta: number;
  buy_volume: number; sell_volume: number; trades: number; absorption: string | null; lot_size: number;
  setup: string | null; reason: string;
};
type Stats = {
  round_trips: number; wins: number; losses: number; win_rate_pct: number; gross_profit: number; gross_loss: number;
  net: number; profit_factor: number; expectancy: number; average_win: number; average_loss: number;
  best: number; worst: number;
  // Bridge to the portfolio's cash-basis realized_pnl:
  //   net - open_position_entry_fees === net_cash_basis === portfolio.realized_pnl
  // OPTIONAL on purpose: the terminal ships ahead of the API, so an older API
  // simply omits them. Rendering `undefined` through money.format() would print
  // "₹NaN", which reads as a broken number rather than a value this build of
  // the API does not publish yet.
  open_position_entry_fees?: number; net_cash_basis?: number; round_trip_rows: { symbol: string; entry: number; exit: number; quantity: number; pnl: number; return_pct: number; entry_time: string; exit_time: string }[];
};
type Book = { orders: Row[]; trades: Row[]; signals: Row[]; equity: { timestamp: string; equity: number }[] };
type Tab = "FLOW" | "AUCTION" | "WATCHLIST" | "POSITIONS" | "ORDERS" | "TRADES" | "STATS";
const TABS: Tab[] = ["FLOW", "AUCTION", "WATCHLIST", "POSITIONS", "ORDERS", "TRADES", "STATS"];
const TAB_STORE = "macd.mpTab";
const FOCUS_STORE = "macd.mpFocus";
// Safari's private mode throws on both halves of localStorage, and a rejected
// preference must not take the desk down with it.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { return; } };

export function MarketProfilePage() {
  const [snap, setSnap] = useState<Snap>();
  const [board, setBoard] = useState<Leader[]>([]);
  const [book, setBook] = useState<Book>();
  const [stats, setStats] = useState<Stats>();
  const [fullProfile, setFullProfile] = useState<{ profile: ProfileData; receivedAt: number }>();
  const [fullProfileErrorKey, setFullProfileErrorKey] = useState("");
  const [focus, setFocus] = useState(() => recall(FOCUS_STORE));
  const [tab, setTab] = useState<Tab>(() => TABS.find(k => k === recall(TAB_STORE)) ?? "FLOW");
  const [error, setError] = useState("");
  const inflight = useRef(false);
  const pickFocus = (symbol: string) => { setFocus(symbol); remember(FOCUS_STORE, symbol); };
  const pickTab = (next: Tab) => { setTab(next); remember(TAB_STORE, next); };

  // A remembered contract survives only while the desk still carries it: an
  // expired series would otherwise poll /api/mp/snapshot for a symbol that has
  // no profile and pin the page on an empty auction until someone noticed.
  useEffect(() => {
    if (!focus || !snap) return;
    const known = [...(snap.subscribed_symbols || []), ...snap.tracked_symbols];
    if (known.length && !known.includes(focus)) pickFocus("");
  }, [focus, snap]);

  useEffect(() => {
    let stopped = false;
    const load = () => {
      if (document.hidden || inflight.current) return;
      inflight.current = true;
      const q = focus ? `?symbol=${encodeURIComponent(focus)}` : "";
      const requests: Promise<unknown>[] = [fetch(`${API_URL}/api/mp/snapshot${q}`, { headers })
        .then(r => r.ok ? r.json() : Promise.reject(new Error("desk unavailable")))
        .then(d => { if (!stopped) { setSnap(d as Snap); setError(""); } })
        .catch(e => { if (!stopped) setError(e instanceof Error ? e.message : "desk unavailable"); })];
      if (tab === "WATCHLIST") requests.push(fetch(`${API_URL}/api/mp/leaderboard?limit=200`, { headers })
        .then(r => r.ok ? r.json() : []).then(d => { if (!stopped) setBoard(d as Leader[]); }).catch(() => undefined));
      // Book + statistics load on EVERY poll, not only when their own tab is
      // open. These feed the tab COUNTS, so gating them on the active tab made
      // every badge read 0 until you clicked into it — the desk looked empty
      // while it actually held six round trips and four open positions. The
      // MACD lane's counts are always live; this is what makes MP's match.
      requests.push(fetch(`${API_URL}/api/mp/book`, { headers })
        .then(r => r.ok ? r.json() : undefined).then(d => { if (!stopped && d) setBook(d as Book); }).catch(() => undefined));
      requests.push(fetch(`${API_URL}/api/mp/statistics`, { headers })
        .then(r => r.ok ? r.json() : undefined).then(d => { if (!stopped && d) setStats(d as Stats); }).catch(() => undefined));
      void Promise.all(requests).finally(() => { inflight.current = false; });
    };
    load();
    const t = window.setInterval(load, 5000);
    return () => { stopped = true; clearInterval(t); };
  }, [focus, tab]);

  // The compact desk snapshot intentionally publishes only a short ladder.
  // Fetch the full focused session while this tab is visible so the price
  // distribution is never inferred from a sampled set of rows.
  useEffect(() => {
    const symbol = snap?.profile?.symbol;
    const day = snap?.profile?.day;
    if (tab !== "AUCTION" || !symbol || !day) return;
    const key = `${symbol}|${day}`;
    let stopped = false;
    let inflight = false;
    const load = () => {
      if (document.hidden || inflight) return;
      inflight = true;
      void fetch(`${API_URL}/api/mp/profile/${encodeURIComponent(symbol)}?full=true&profile_only=true`, { headers })
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(d => {
          const profile = d?.profile as ProfileData | undefined;
          if (!profile || profile.symbol !== symbol || profile.day !== day) throw new Error("Profile session mismatch");
          if (!stopped) {
            setFullProfile({ profile, receivedAt: Date.now() });
            setFullProfileErrorKey("");
          }
        })
        .catch(() => {
          if (!stopped) {
            setFullProfile(undefined);
            setFullProfileErrorKey(key);
          }
        })
        .finally(() => { inflight = false; });
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => { stopped = true; clearInterval(timer); };
  }, [tab, snap?.profile?.symbol, snap?.profile?.day]);

  // If a full response stalls, the developing ladder must return to the
  // latest compact snapshot instead of holding yesterday's last good rows.
  useEffect(() => {
    if (!fullProfile) return;
    const timer = window.setTimeout(() => {
      setFullProfile(current => current === fullProfile ? undefined : current);
      setFullProfileErrorKey(`${fullProfile.profile.symbol}|${fullProfile.profile.day}`);
    }, Math.max(0, 15_000 - (Date.now() - fullProfile.receivedAt)));
    return () => clearTimeout(timer);
  }, [fullProfile]);

  const pf = snap?.portfolio;
  const counts: Record<Tab, number | ""> = {
    // The leaderboard stays lazy (200 rows), so fall back to the snapshot's own
    // tracked-symbol count rather than showing 0 for a populated watchlist.
    FLOW: "", AUCTION: "", WATCHLIST: board.length || (snap?.tracked_symbols.length ?? 0),
    POSITIONS: pf?.positions.length ?? 0,
    ORDERS: book?.orders.length ?? 0, TRADES: book?.trades.length ?? 0, STATS: stats?.round_trips ?? 0,
  };
  const currentFullProfile = fullProfile
    && fullProfile.profile.symbol === snap?.profile?.symbol
    && fullProfile.profile.day === snap?.profile?.day
    && Date.now() - fullProfile.receivedAt < 15_000 ? fullProfile.profile : undefined;

  return <section className="ledger-page mp-page">
    <div className="page-heading">
      <div><h1>Market Profile &amp; Order Flow</h1>
        <p>TPO auction structure and quote-rule order flow from the Fyers tick feed · independent paper book
          {snap ? ` · ${snap.prints_seen.toLocaleString("en-IN")} prints · ${snap.tracked_symbols.length} of ${snap.subscribed_count ?? snap.tracked_symbols.length} contracts traded today · ${snap.trades_today} desk trades` : ""}</p>
        {snap && snap.tracked_symbols.length < (snap.subscribed_count ?? 0) / 2 && <p className="mp-hint">
          Only instruments that have traded today build a profile — the rest are subscribed and waiting.
          Before 09:15 that is normally just the indices.</p>}</div>
      <div className="rrg-controls">
        <span className={snap?.health?.ready_for_auto_paper ? "feed-badge" : "mode"} title={snap?.health?.blockers.join(" · ")}>
          {snap?.settings.auto_trade ? "AUTO PAPER" : "PAPER OBSERVE"} · {snap?.health?.session_open ? (snap?.health?.ready_for_auto_paper ? "READY" : "DEGRADED") : "SESSION CLOSED"} · {snap?.settings.allow_overnight_carry ? "SELECTIVE CARRY" : "INTRADAY"}
        </span>
        <select className="rrg-select" value={focus} onChange={e => pickFocus(e.target.value)}>
          <option value="">Auto (most active)</option>
          {(snap?.tracked_symbols || []).map(s => <option key={s} value={s}>{short(s)}</option>)}
          {/* This list is only what has PRINTED today, while the focus can be
              any subscribed contract — a remembered one, or one picked from the
              order-flow toolbar. Without its own option the select renders
              blank until the contract's first trade. */}
          {focus && !(snap?.tracked_symbols || []).includes(focus) && <option value={focus}>{short(focus)}</option>}
        </select>
      </div>
    </div>
    {/* `main-tabs` (not the small `watch-tabs`) so the desk's tabs render at the
        same weight as the MACD lane's — this lane is a peer, not a sub-view. */}
    <nav className="main-tabs mp-main-tabs">
      {TABS.map(k =>
        <button key={k} className={tab === k ? "active" : ""} onClick={() => pickTab(k)}>
          {k === "FLOW" ? "Order flow" : k === "STATS" ? "Trade statistics" : k.charAt(0) + k.slice(1).toLowerCase()}{counts[k] !== "" && <span>{counts[k]}</span>}</button>)}
    </nav>
    {error && <div className="settings-error">{error}</div>}
    {snap && !snap.enabled && <div className="settings-error">The Market-Profile desk is disabled (MACD_MP_ENABLED=false).</div>}
    {snap?.health?.session_open && snap.health.blockers.length > 0 && <div className="settings-error">
      Desk readiness: {snap.health.blockers.join(" · ")}. Quote-rule coverage {((snap.classification?.quote_share ?? 0) * 100).toFixed(1)}%.
    </div>}

    <div className="mp-body">
      {tab === "FLOW" && <OrderFlowWorkspace
        symbols={snap?.subscribed_symbols?.length ? snap.subscribed_symbols : (snap?.tracked_symbols || [])}
        traded={snap?.tracked_symbols || []}
        focus={focus || snap?.focus || ""}
        onFocus={pickFocus} onOpenAuction={() => pickTab("AUCTION")} />}
      {tab === "AUCTION" && <AuctionView snap={snap}
        fullProfile={currentFullProfile}
        fullProfileUnavailable={fullProfileErrorKey === `${snap?.profile?.symbol}|${snap?.profile?.day}`} />}
      {tab === "WATCHLIST" && <Watchlist rows={board} onPick={s => { pickFocus(s); pickTab("AUCTION"); }} />}
      {tab === "POSITIONS" && <Positions positions={pf?.positions || []} closed={snap?.closed_positions || []} entryState={snap?.entry_state || {}} portfolio={pf}
        retentionHour={snap?.closed_positions_retention?.until_hour_ist ?? 6} />}
      {tab === "ORDERS" && <Orders rows={book?.orders || []} />}
      {tab === "TRADES" && <Trades fills={book?.trades || []} rounds={stats?.round_trip_rows || []} />}
      {tab === "STATS" && <StatsView stats={stats} equity={book?.equity || []} portfolio={pf} signals={book?.signals || []} />}
    </div>
  </section>;
}

/* ---------------------------------------------------------------- auction */

function AuctionView({ snap, fullProfile, fullProfileUnavailable }: { snap?: Snap; fullProfile?: ProfileData; fullProfileUnavailable: boolean }) {
  const profile = fullProfile || snap?.profile;
  const flow = snap?.flow;
  const partialCapture = profile?.partial_capture === true || (profile?.first_bracket ?? 0) > 0;
  const maxFoot = useMemo(() => Math.max(1, ...(flow?.footprint || []).map(f => f.buy + f.sell)), [flow]);

  return <div className="mp-layout">
    <div className="panel mp-profile-panel">
      <div className="panel-title"><span>{profile ? short(profile.symbol) : "Waiting for prints"}</span>
        <span>{profile ? `${profile.brackets} observed ${profile.brackets === 1 ? "bracket" : "brackets"} · ${profile.day}` : ""}</span></div>
      {!profile ? <div className="chart-empty">No profile yet — profiles build from live ticks during market hours.</div>
        : <div className="mp-profile-body">
          {/* The same strip the FLOW tab carries, from the same builder: a
              reader moving between the two tabs must not have to re-establish
              what kind of day it is. */}
          {snap?.session && <div className="mp-session-badges"><SessionBadges session={snap.session} /></div>}
          <div className="mp-stats">
            <Stat label="Open type" value={words(profile.open_type)}
              hint={profile.open_observed === false ? "The 09:15-09:30 window was not observed this session" : undefined} />
            <Stat label="Day type" value={words(profile.day_type) + (profile.ib_complete === false && !partialCapture ? " *" : "")}
              hint={partialCapture ? "Initial balance was not observed; day type cannot be inferred"
                : profile.ib_complete === false ? "Initial balance is still forming" : undefined} />
            <Stat label="Position" value={words(profile.position)} />
            <Stat label={partialCapture ? "Captured POC" : "TPO POC"} value={profile.poc != null ? `₹${money.format(profile.poc)}` : "—"} />
            <Stat label={partialCapture ? "Captured VA" : "Value area"} value={profile.vah != null && profile.val != null ? `₹${money.format(profile.val)} – ₹${money.format(profile.vah)}` : "—"} />
            <Stat label="Initial balance" value={profile.ib_high != null && profile.ib_low != null ? `₹${money.format(profile.ib_low)} – ₹${money.format(profile.ib_high)}` : "—"} />
          </div>
          {fullProfileUnavailable && <div className="mp-ladder-warning">
            Full ladder update unavailable; showing {profile.levels_sampled ? "a sampled" : "the current"} desk snapshot.
          </div>}
          <MarketProfileLadder profile={profile} />
        </div>}
    </div>

    <aside className="panel mp-side">
      <div className="panel-title">Estimated order flow <span>{flow ? `${flow.trades} prints` : "—"}</span></div>
      {!flow ? <div className="chart-empty">No flow yet</div> : <div className="mp-flow">
        <div className="mp-stats compact">
          <Stat label="Cumulative delta" value={money.format(flow.cumulative_delta)} tone={flow.cumulative_delta} />
          <Stat label="Imbalance" value={`${(flow.imbalance * 100).toFixed(1)}%`} tone={flow.imbalance} />
          <Stat label="Buy volume" value={money.format(flow.buy_volume)} />
          <Stat label="Sell volume" value={money.format(flow.sell_volume)} />
        </div>
        <CvdChart rows={flow.cvd_curve} />
        <div className="mp-tags">
          {flow.absorption.detected && <span className={`mp-tag ${flow.absorption.side === "sellers_absorbed" ? "good" : "bad"}`}>{words(flow.absorption.side)}</span>}
          {flow.divergence.detected && <span className={`mp-tag ${flow.divergence.kind === "bullish" ? "good" : "bad"}`}>{flow.divergence.kind} divergence</span>}
          {snap?.setup?.setup && <span className="mp-tag setup">{words(snap.setup.setup)}</span>}
        </div>
        {snap?.setup && <p className="field-help mp-reason">{snap.setup.reason}</p>}
        <div className="mp-foot-head"><span>Price</span><span>Sell</span><span>Buy</span><span>Δ</span></div>
        <div className="mp-footprint">
          {flow.footprint.map(f => <div key={f.price} className="mp-foot-row">
            <span>{money.format(f.price)}</span>
            <span className="negative">{money.format(f.sell)}</span>
            <span className="positive">{money.format(f.buy)}</span>
            <span className={f.delta >= 0 ? "positive" : "negative"}>{money.format(f.delta)}</span>
            <span className="mp-foot-bar" style={{ width: `${((f.buy + f.sell) / maxFoot) * 100}%` }} />
          </div>)}
        </div>
      </div>}
    </aside>
  </div>;
}

function CvdChart({ rows }: { rows: { t: number; cvd: number; price: number }[] }) {
  const element = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; cvd: ISeriesApi<"Line">; price: ISeriesApi<"Line"> }>();
  useEffect(() => {
    if (!element.current) return;
    const chart = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#0b1018" }, textColor: "#8796aa", attributionLogo: false },
      grid: { vertLines: { color: "#141d29" }, horzLines: { color: "#141d29" } },
      rightPriceScale: { borderColor: "#263144" },
      leftPriceScale: { visible: true, borderColor: "#263144" },
      timeScale: { borderColor: "#263144", timeVisible: true, secondsVisible: false,
        tickMarkFormatter: (time: Time) => formatCvdTime(time) },
      localization: { timeFormatter: (time: Time) => `${formatCvdTime(time, true)} IST` },
      handleScroll: false, handleScale: false,
    });
    const cvd = chart.addSeries(LineSeries, { color: "#4d91ff", lineWidth: 2, title: "CVD", priceScaleId: "right" });
    const price = chart.addSeries(LineSeries, { color: "#f5b84b", lineWidth: 1, title: "Premium", priceScaleId: "left" });
    api.current = { chart, cvd, price };
    const observer = new ResizeObserver(() => chart.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.remove(); };
  }, []);
  useEffect(() => {
    if (!api.current || !rows.length) return;
    // The tick stream can deliver several prints inside one second; the chart
    // requires strictly increasing times, so keep the last value per second.
    const byTime = new Map<number, { cvd: number; price: number }>();
    rows.forEach(r => byTime.set(r.t, { cvd: r.cvd, price: r.price }));
    const ordered = [...byTime.entries()].sort((a, b) => a[0] - b[0]);
    api.current.cvd.setData(ordered.map(([t, v]) => ({ time: t as Time, value: v.cvd })));
    api.current.price.setData(ordered.map(([t, v]) => ({ time: t as Time, value: v.price })));
    api.current.chart.timeScale().fitContent();
  }, [rows]);
  return <div className="mp-cvd">
    <div className="mp-cvd-title">Cumulative delta vs premium · IST</div>
    <div ref={element} className="mp-cvd-chart" />
    {!rows.length && <div className="chart-empty">No prints yet</div>}
  </div>;
}

/* -------------------------------------------------------------- watchlist */

type Col<T> = { key: string; label: string; value: (r: T) => string | number; render?: (r: T) => ReactNode; tone?: (r: T) => string };

function SortTable<T>({ rows, cols, empty, initial, onRow }: { rows: T[]; cols: Col<T>[]; empty: string; initial: string; onRow?: (r: T) => void }) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: initial, dir: "desc" });
  const sorted = useMemo(() => {
    const col = cols.find(c => c.key === sort.key) || cols[0];
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = col.value(a), bv = col.value(b);
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv), undefined, { numeric: true }) * dir;
    });
  }, [rows, cols, sort]);
  return <div className="ledger-table-wrap"><table className="ledger-table"><thead><tr>
    {cols.map(c => <th key={c.key}><button onClick={() => setSort(s => ({ key: c.key, dir: s.key === c.key && s.dir === "desc" ? "asc" : "desc" }))}>
      {c.label}<span className={sort.key === c.key ? "sort active" : "sort"}>{sort.key === c.key ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}</span></button></th>)}
  </tr></thead><tbody>
    {sorted.length ? sorted.map((r, i) => <tr key={i} onClick={() => onRow?.(r)} style={onRow ? { cursor: "pointer" } : undefined}>
      {cols.map(c => <td key={c.key} className={c.tone?.(r) || ""}>{c.render ? c.render(r) : String(c.value(r))}</td>)}
    </tr>) : <tr><td colSpan={cols.length} className="empty">{empty}</td></tr>}
  </tbody></table></div>;
}

function Watchlist({ rows, onPick }: { rows: Leader[]; onPick: (s: string) => void }) {
  const cols: Col<Leader>[] = [
    { key: "symbol", label: "Contract", value: r => r.symbol, render: r => short(r.symbol) },
    { key: "last", label: "LTP", value: r => r.last, render: r => `₹${money.format(r.last)}` },
    { key: "poc", label: "POC", value: r => r.poc ?? 0, render: r => r.poc != null ? `₹${money.format(r.poc)}` : "—" },
    { key: "value", label: "Value area", value: r => r.val ?? 0, render: r => r.val != null && r.vah != null ? `${money.format(r.val)}–${money.format(r.vah)}` : "—" },
    { key: "ib", label: "Initial balance", value: r => r.ib_low ?? 0, render: r => r.ib_low != null && r.ib_high != null ? `${money.format(r.ib_low)}–${money.format(r.ib_high)}` : "—" },
    { key: "position", label: "Position", value: r => r.position, render: r => words(r.position) },
    { key: "day_type", label: "Day type", value: r => r.day_type, render: r => words(r.day_type) },
    { key: "open_type", label: "Open type", value: r => r.open_type, render: r => words(r.open_type) },
    { key: "imbalance", label: "Imbalance", value: r => r.imbalance, render: r => `${(r.imbalance * 100).toFixed(0)}%`, tone: r => r.imbalance >= 0 ? "positive" : "negative" },
    { key: "cvd", label: "CVD", value: r => r.cumulative_delta, render: r => money.format(r.cumulative_delta), tone: r => r.cumulative_delta >= 0 ? "positive" : "negative" },
    { key: "prints", label: "Prints", value: r => r.trades },
    { key: "absorption", label: "Absorption", value: r => r.absorption || "", render: r => words(r.absorption) },
    { key: "setup", label: "Setup", value: r => r.setup || "", render: r => r.setup ? <span className="mp-tag setup">{words(r.setup)}</span> : "—" },
  ];
  return <div className="panel mp-full"><SortTable rows={rows} cols={cols} initial="setup" empty="No contracts tracked yet — the desk populates from the live tick stream." onRow={r => onPick(r.symbol)} /></div>;
}

/* -------------------------------------------------------- book sub-pages */

const excursionTitle = (price?: number | null, at?: string | null) =>
  price == null ? undefined : `₹${money.format(price)}${at ? ` at ${clock(at)}` : ""}`;
function excursionCols<T extends Excursion>(): Col<T>[] {
  return [
    { key: "mfe", label: "Max %", value: r => r.max_return_pct ?? 0,
      render: r => <span title={excursionTitle(r.max_price, r.max_at)}>{pct(r.max_return_pct)}</span>,
      tone: r => (r.max_return_pct ?? 0) >= 0 ? "positive" : "negative" },
    { key: "mae", label: "Min %", value: r => r.min_return_pct ?? 0,
      render: r => <span title={excursionTitle(r.min_price, r.min_at)}>{pct(r.min_return_pct)}</span>,
      tone: r => (r.min_return_pct ?? 0) >= 0 ? "positive" : "negative" },
  ];
}

function Positions({ positions, closed, entryState, portfolio, retentionHour }: {
  positions: Position[]; closed: ClosedPosition[]; entryState: Snap["entry_state"]; portfolio?: Snap["portfolio"]; retentionHour: number;
}) {
  const cols: Col<Position>[] = [
    { key: "symbol", label: "Contract", value: r => r.symbol, render: r => short(r.symbol) },
    { key: "setup", label: "Setup", value: r => entryState[r.symbol]?.setup || "", render: r => words(entryState[r.symbol]?.setup) },
    { key: "opened", label: "Entry time (IST)", value: r => Date.parse(r.opened_at || "") || 0, render: r => r.opened_at ? stamp(r.opened_at) : "—" },
    { key: "lots", label: "Lots", value: r => r.lots, render: r => `${r.lots} × ${r.lot_size}` },
    { key: "average", label: "Average", value: r => r.average_price, render: r => `₹${money.format(r.average_price)}` },
    { key: "ltp", label: "LTP", value: r => r.last_price, render: r => `₹${money.format(r.last_price)}` },
    { key: "stop", label: "Hard stop", value: r => r.hard_stop ?? 0, render: r => r.hard_stop ? `₹${money.format(r.hard_stop)}` : "—" },
    { key: "trail", label: "Trailing stop", value: r => r.trailing_stop ?? 0, render: r => r.trailing_stop ? `₹${money.format(r.trailing_stop)}` : "Not armed" },
    { key: "carry", label: "Carry decision", value: r => entryState[r.symbol]?.carry?.allowed ? 1 : 0, render: r => {
      const carry = entryState[r.symbol]?.carry;
      return carry ? <span title={carry.reason} className={carry.allowed ? "positive" : "negative"}>{carry.allowed ? "Carry" : "Exit"}</span> : "Pending close review";
    } },
    { key: "pnl", label: "Unrealized", value: r => r.unrealized_pnl, render: r => `₹${money.format(r.unrealized_pnl)}`, tone: r => r.unrealized_pnl >= 0 ? "positive" : "negative" },
    { key: "pct", label: "Return %", value: r => r.average_price ? (r.last_price / r.average_price - 1) * 100 : 0, render: r => `${(r.average_price ? (r.last_price / r.average_price - 1) * 100 : 0).toFixed(2)}%`, tone: r => r.last_price >= r.average_price ? "positive" : "negative" },
    ...excursionCols<Position>(),
  ];
  const closedCols: Col<ClosedPosition>[] = [
    { key: "exit_time", label: "Closed (IST)", value: r => Date.parse(r.exit_time) || 0, render: r => clock(r.exit_time) },
    { key: "symbol", label: "Contract", value: r => r.symbol, render: r => short(r.symbol) },
    { key: "setup", label: "Setup", value: r => r.setup || "", render: r => words(r.setup) },
    { key: "entry", label: "Entry", value: r => r.entry_price, render: r => <span title={stamp(r.entry_time)}>{`₹${money.format(r.entry_price)} · ${clock(r.entry_time)}`}</span> },
    { key: "exit", label: "Exit", value: r => r.exit_price, render: r => `₹${money.format(r.exit_price)}` },
    { key: "lots", label: "Lots", value: r => r.lots, render: r => `${r.lots} × ${r.lot_size}${r.partial ? " (partial)" : ""}` },
    { key: "pnl", label: "P&L", value: r => r.pnl, render: r => `₹${money.format(r.pnl)}`, tone: r => r.pnl >= 0 ? "positive" : "negative" },
    { key: "ret", label: "Return %", value: r => r.return_pct, render: r => pct(r.return_pct), tone: r => r.return_pct >= 0 ? "positive" : "negative" },
    ...excursionCols<ClosedPosition>(),
    { key: "reason", label: "Exit reason", value: r => r.exit_reason,
      render: r => <span className={`mp-tag ${r.pnl >= 0 ? "good" : "bad"}`}>{words(r.exit_reason.replace(/^MP_/, ""))}</span> },
  ];
  const invested = positions.reduce((s, p) => s + p.average_price * p.quantity, 0);
  const value = positions.reduce((s, p) => s + p.last_price * p.quantity, 0);
  const closedPnl = closed.reduce((s, r) => s + r.pnl, 0);
  const until = `${String(retentionHour).padStart(2, "0")}:00 IST`;
  return <div className="panel mp-full mp-split mp-positions-split">
    <section className="book-section"><div className="book-heading"><b>Open positions</b><span>{positions.length}</span></div>
      <div className="book-body"><SortTable rows={positions} cols={cols} initial="pnl" empty="No open desk positions" /></div></section>
    <section className="book-section"><div className="book-heading"><b>Closed today</b>
      <span>{closed.length ? `${closed.length} · shown until ${until} tomorrow` : "—"}</span></div>
      <div className="book-body"><SortTable rows={closed} cols={closedCols} initial="exit_time" empty={`Nothing closed since ${until}`} /></div></section>
    <div className="summary-strip">
      <b className="summary-title">Desk position summary</b>
      <div><span>Open / closed today</span><b>{positions.length} / {closed.length}</b></div>
      <div><span>Invested</span><b>₹{money.format(invested)}</b></div>
      <div><span>Current value</span><b>₹{money.format(value)}</b></div>
      <div><span>Unrealized</span><b className={(portfolio?.unrealized_pnl || 0) >= 0 ? "positive" : "negative"}>₹{money.format(portfolio?.unrealized_pnl || 0)}</b></div>
      <div><span>Closed today P&amp;L</span><b className={closedPnl >= 0 ? "positive" : "negative"}>₹{money.format(closedPnl)}</b></div>
    </div>
  </div>;
}

function Orders({ rows }: { rows: Row[] }) {
  const cols: Col<Row>[] = [
    { key: "time", label: "Time (IST)", value: r => Date.parse(String(r.created_at)) || 0, render: r => r.created_at ? stamp(String(r.created_at)) : "—" },
    { key: "symbol", label: "Contract", value: r => String(r.symbol), render: r => short(String(r.symbol)) },
    { key: "side", label: "Side", value: r => String(r.side), tone: r => r.side === "BUY" ? "positive" : "negative" },
    { key: "lots", label: "Lots", value: r => Number(r.lots) || 0 },
    { key: "qty", label: "Units", value: r => Number(r.quantity) || 0, render: r => `${r.quantity} (${r.lot_size}/lot)` },
    { key: "fill", label: "Fill", value: r => Number(r.fill_price) || 0, render: r => r.fill_price ? `₹${money.format(Number(r.fill_price))}` : "—" },
    { key: "status", label: "Status", value: r => String(r.status) },
    { key: "broker", label: "Broker ref", value: r => String(r.broker_order_id || "") },
  ];
  return <div className="panel mp-full"><SortTable rows={rows} cols={cols} initial="time" empty="No desk orders yet — enable auto-trade or place a manual ticket." /></div>;
}

function Trades({ fills, rounds }: { fills: Row[]; rounds: Stats["round_trip_rows"] }) {
  const fillCols: Col<Row>[] = [
    { key: "time", label: "Time (IST)", value: r => Date.parse(String(r.timestamp)) || 0, render: r => r.timestamp ? stamp(String(r.timestamp)) : "—" },
    { key: "symbol", label: "Contract", value: r => String(r.symbol), render: r => short(String(r.symbol)) },
    { key: "side", label: "Side", value: r => String(r.side), tone: r => r.side === "BUY" ? "positive" : "negative" },
    { key: "qty", label: "Units", value: r => Number(r.quantity) || 0 },
    { key: "price", label: "Fill price", value: r => Number(r.price) || 0, render: r => `₹${money.format(Number(r.price))}` },
    { key: "value", label: "Value", value: r => Number(r.price) * Number(r.quantity), render: r => `₹${money.format(Number(r.price) * Number(r.quantity))}` },
  ];
  const roundCols: Col<Stats["round_trip_rows"][number]>[] = [
    { key: "exit_time", label: "Closed (IST)", value: r => Date.parse(r.exit_time) || 0, render: r => stamp(r.exit_time) },
    { key: "symbol", label: "Contract", value: r => r.symbol, render: r => short(r.symbol) },
    { key: "entry", label: "Entry", value: r => r.entry, render: r => `₹${money.format(r.entry)}` },
    { key: "exit", label: "Exit", value: r => r.exit, render: r => `₹${money.format(r.exit)}` },
    { key: "qty", label: "Units", value: r => r.quantity },
    { key: "pnl", label: "P&L", value: r => r.pnl, render: r => `₹${money.format(r.pnl)}`, tone: r => r.pnl >= 0 ? "positive" : "negative" },
    { key: "ret", label: "Return %", value: r => r.return_pct, render: r => `${r.return_pct.toFixed(2)}%`, tone: r => r.return_pct >= 0 ? "positive" : "negative" },
  ];
  return <div className="panel mp-full mp-split">
    <section className="book-section"><div className="book-heading"><b>Completed round trips</b><span>{rounds.length}</span></div>
      <div className="book-body"><SortTable rows={rounds} cols={roundCols} initial="exit_time" empty="No completed round trips yet" /></div></section>
    <section className="book-section"><div className="book-heading"><b>Individual fills</b><span>{fills.length}</span></div>
      <div className="book-body"><SortTable rows={fills} cols={fillCols} initial="time" empty="No desk fills yet" /></div></section>
  </div>;
}

function StatsView({ stats, equity, portfolio, signals }: { stats?: Stats; equity: { timestamp: string; equity: number }[]; portfolio?: Snap["portfolio"]; signals: Row[] }) {
  const element = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; line: ISeriesApi<"Area"> }>();
  useEffect(() => {
    if (!element.current) return;
    const chart = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#0b1018" }, textColor: "#8796aa", attributionLogo: false },
      grid: { vertLines: { color: "#141d29" }, horzLines: { color: "#141d29" } },
      rightPriceScale: { borderColor: "#263144" },
      timeScale: { borderColor: "#263144", timeVisible: true, secondsVisible: false },
      localization: { locale: "en-IN", priceFormatter: (v: number) => `₹${money.format(v)}` },
    });
    const line = chart.addSeries(AreaSeries, { lineColor: "#22c893", topColor: "#22c89344", bottomColor: "#22c89308", lineWidth: 2, title: "Desk equity" });
    api.current = { chart, line };
    const observer = new ResizeObserver(() => chart.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.remove(); };
  }, []);
  useEffect(() => {
    if (!api.current || !equity.length) return;
    const byTime = new Map<number, number>();
    equity.forEach(r => byTime.set(Math.floor(Date.parse(r.timestamp) / 1000), r.equity));
    api.current.line.setData([...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ time: t as Time, value: v })));
    api.current.chart.timeScale().fitContent();
  }, [equity]);

  const cells: [string, string, number?][] = stats ? [
    ["Round trips", String(stats.round_trips)],
    ["Win rate", `${stats.win_rate_pct.toFixed(1)}%`],
    ["Winners / losers", `${stats.wins} / ${stats.losses}`],
    ["Net P&L (round trips)", `₹${money.format(stats.net)}`, stats.net],
    ["Profit factor", Number.isFinite(stats.profit_factor) ? stats.profit_factor.toFixed(2) : "∞"],
    ["Expectancy / trade", `₹${money.format(stats.expectancy)}`, stats.expectancy],
    ["Gross profit", `₹${money.format(stats.gross_profit)}`, 1],
    ["Gross loss", `₹${money.format(stats.gross_loss)}`, -1],
    ["Average win", `₹${money.format(stats.average_win)}`, 1],
    ["Average loss", `₹${money.format(stats.average_loss)}`, -1],
    ["Best", `₹${money.format(stats.best)}`, stats.best],
    ["Worst", `₹${money.format(stats.worst)}`, stats.worst],
    // The bridge, shown explicitly. "Net P&L (round trips)" charges each closed
    // trip only its pro-rata entry fee, so fees already paid on STILL-OPEN
    // positions sit outside it. The book's Realized figure is cash-basis and has
    // paid all of them. Both are right; showing them adjacent without the
    // bridge is what made the two look like they disagreed.
    ...(stats.open_position_entry_fees === undefined ? [] : [
      ["Entry fees on open positions", `₹${money.format(stats.open_position_entry_fees)}`, -1],
    ] as [string, string, number?][]),
    ...(stats.net_cash_basis === undefined ? [] : [
      ["Net P&L (cash basis)", `₹${money.format(stats.net_cash_basis)}`, stats.net_cash_basis],
    ] as [string, string, number?][]),
    ["Desk equity", `₹${money.format(portfolio?.equity || 0)}`],
    [stats.net_cash_basis === undefined ? "Realized" : "Realized (matches cash basis)",
     `₹${money.format(portfolio?.realized_pnl || 0)}`, portfolio?.realized_pnl],
    ["Unrealized", `₹${money.format(portfolio?.unrealized_pnl || 0)}`, portfolio?.unrealized_pnl],
    ["Signals logged", String(signals.length)],
  ] : [];
  return <div className="mp-stats-page">
    <div className="panel mp-stats-grid">
      <div className="panel-title">Desk performance <span>independent of the MACD book</span></div>
      <div className="statistics-grid mp-metrics">{cells.map(([label, value, tone]) => <div key={label}>
        <span>{label}</span><b className={tone === undefined ? "" : tone >= 0 ? "positive" : "negative"}>{value}</b></div>)}</div>
    </div>
    <div className="panel mp-equity">
      <div className="panel-title">Desk equity curve <span>{equity.length} samples</span></div>
      <div ref={element} className="mp-equity-chart" />
      {!equity.length && <div className="chart-empty">No equity samples yet — the curve records on every desk fill.</div>}
    </div>
  </div>;
}

function Stat({ label, value, tone, hint }: { label: string; value: string; tone?: number; hint?: string }) {
  return <div className={hint ? "mp-stat caveat" : "mp-stat"} title={hint}><span>{label}</span>
    <b className={tone === undefined ? "" : tone >= 0 ? "positive" : "negative"}>{value}</b></div>;
}
