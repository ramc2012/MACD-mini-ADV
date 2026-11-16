import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { niftyFirst } from "./defaults";
import { DEFAULT_LAYERS, DEFAULT_PANES, TradingChart, type PriceStyle } from "./Chart";
import { chartSourceStatus } from "./chartSource";
import { useChartReferences } from "./useChartReferences";
import { closeStudies, mergeLiveCandles, sessionCutoff } from "./chartMath";
import { fillMarkers, positionLevels } from "./laneChartMarks";
import { PositionChartModal } from "./PositionChartModal";
import { SettingsPanel } from "./SettingsPanel";
import { TradingLedger, type LedgerTab } from "./Ledger";
import { DispersionPage, useDispersionSeries } from "./Dispersion";
import { API_TOKEN, API_URL, MI_URL } from "./runtime";

// Pages opened from the tab bar load on first visit, so the trading view
// does not wait for the footprint, profile, research and analytics code.
const EquityCurve = lazy(() => import("./EquityCurve").then((m) => ({ default: m.EquityCurve })));
const TrendRotation = lazy(() => import("./TrendRotation").then((m) => ({ default: m.TrendRotation })));
const RRGPage = lazy(() => import("./RRG").then((m) => ({ default: m.RRGPage })));
const MarketProfilePage = lazy(() => import("./MarketProfile").then((m) => ({ default: m.MarketProfilePage })));
const SignalRadar = lazy(() => import("./SignalRadar").then((m) => ({ default: m.SignalRadar })));
const RatioPage = lazy(() => import("./RatioChart").then((m) => ({ default: m.RatioPage })));
const StraddlePage = lazy(() => import("./StraddlePage").then((m) => ({ default: m.StraddlePage })));
const AuctionPage = lazy(() => import("./Auction").then((m) => ({ default: m.AuctionPage })));
const OptionsDeskPage = lazy(() => import("./OptionsDesk").then((m) => ({ default: m.OptionsDeskPage })));
const BlastLanePage = lazy(() => import("./BlastLane").then((m) => ({ default: m.BlastLanePage })));
const QuantAnalyticsPage = lazy(() => import("./QuantAnalytics").then((m) => ({ default: m.QuantAnalyticsPage })));
const AgentPage = lazy(() => import("./AgentPage").then((m) => ({ default: m.AgentPage })));
const AgenticPage = lazy(() => import("./AgenticPage").then((m) => ({ default: m.AgenticPage })));
const LanesPage = lazy(() => import("./LanesPage").then((m) => ({ default: m.LanesPage })));
const ScoreboardPage = lazy(() => import("./ScoreboardPage").then((m) => ({ default: m.ScoreboardPage })));
const SpotLanePage = lazy(() => import("./SpotLanePage").then((m) => ({ default: m.SpotLanePage })));
const NomadLanePage = lazy(() => import("./NomadLanePage").then((m) => ({ default: m.NomadLanePage })));
const UpstoxLanePage = lazy(() => import("./UpstoxLanePage").then((m) => ({ default: m.UpstoxLanePage })));
const McxLanePage = lazy(() => import("./McxLane").then((m) => ({ default: m.McxLanePage })));
import { Readiness } from "./Readiness";
import { PortfolioRisk } from "./PortfolioRisk";
import { BookGreeks } from "./BookGreeks";
import { LedgerCounts } from "./LedgerCounts";
import { AtmHeatmap } from "./AtmHeatmap";
import { SectorWheel } from "./SectorWheel";
import { SectorCoil } from "./SectorCoil";
import { DayHighs } from "./DayHighs";
import { matchesWatchSearch, quoteStamp } from "./watchMath";
import { sessionState, tokenLeft } from "./sessionClock";
import { requestJson } from "./requestJson";
import { useStream } from "./useStream";
import { optionRowsForTab, WATCH_TABS, type WatchTab } from "./watchTabs";
import { observedBrokerStatus } from "./feedStatus";
import type { BlastCandidate, BlastSnapshot, Candle, ChartLayers, ChartMarker, EquityPoint, HealthInfo, Indicator, OptionWatchRow, Order, PaneCollapse, Portfolio, ResearchOpenPosition, ResearchSummary, ResearchTrade, Signal, Snapshot, SpotWatchRow, StreamEvent, Tick, Trade } from "./types";
import { PlatformWorkstation } from "./PlatformWorkstation";

const money = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const emptyPortfolio: Portfolio = { equity: 0, cash: 0, market_value: 0, realized_pnl: 0, unrealized_pnl: 0, positions: [], closed_positions: [] };
type WatchSortKey = "instrument" | "ltp" | "change" | "macd" | "gex" | "volume";
type ChartPeriod = "1D" | "5D" | "1M" | "3M" | "ALL";
// Sessions, not calendar days: a trailing 24 hours at 09:20 showed yesterday
// from 09:20 onward plus a few bars of today, which is not what "1D" means.
const CHART_PERIODS: { key: ChartPeriod; sessions: number }[] = [
  { key: "1D", sessions: 1 }, { key: "5D", sessions: 5 }, { key: "1M", sessions: 22 }, { key: "3M", sessions: 64 }, { key: "ALL", sessions: 0 },
];
const CHART_PERIOD_STORE = "macd.chartPeriod";
const CHART_LAYERS_STORE = "macd.chartLayers";
const CHART_PANES_STORE = "macd.chartPanes";
const CHART_STYLE_STORE = "macd.chartStyle";
// Bar sizes the terminal chart can show (owner, 4 Nov 2026: 3, 5 and 15 minutes too). The lane's own frame streams
// live; any other is aggregated from the stored minutes by /api/chart and refetched every 30 s.
const CHART_FRAME_STORE = "macd.chartFrame";
const CHART_FRAMES: [number, string][] = [[180, "3m"], [300, "5m"], [900, "15m"], [1800, "30m"], [3600, "1h"], [86400, "1D"]];
const SELECTED_SYMBOL_STORE = "macd.selectedSymbol";
// Every localStorage touch goes through these.  Writes throw outright in
// Safari private browsing and with site data blocked, and a throw inside a
// useState initialiser or a commit-phase useEffect unmounts the whole desk --
// App has no error boundary above it.  Losing a preference is acceptable;
// losing the terminal is not.
function readKey(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writeKey(key: string, value: string) {
  try { window.localStorage.setItem(key, value); } catch { /* storage unavailable; the preference simply does not persist */ }
}
function readStore<T extends object>(key: string, fallback: T): T {
  try { const raw = readKey(key); return raw ? { ...fallback, ...JSON.parse(raw) as Partial<T> } : fallback; } catch { return fallback; }
}
const INDEX_UNDERLYINGS = new Set(["NIFTY", "SENSEX", "BANKNIFTY", "MIDCPNIFTY"]);
// The three the desk quotes at each other across the room; universe.py
// INDEX_SPOTS is the source of the symbols.
const HEADER_INDICES: { label: string; short: string; symbol: string }[] = [
  { label: "NIFTY", short: "NIFTY", symbol: "NSE:NIFTY50-INDEX" },
  { label: "BANKNIFTY", short: "BANK", symbol: "NSE:NIFTYBANK-INDEX" },
  { label: "SENSEX", short: "SENSEX", symbol: "BSE:SENSEX-INDEX" },
  { label: "MIDCPNIFTY", short: "MIDCP", symbol: "NSE:MIDCPNIFTY-INDEX" },
];
// A reload used to dump the desk back on the first broker symbol whatever
// it had been charting.  The stored pick is honoured only while the engine
// still subscribes it, so an expired contract cannot strand the chart.
function restoreSelected(symbols: string[]) {
  const stored = readKey(SELECTED_SYMBOL_STORE);
  // Nothing remembered: open on NIFTY (owner, 16 Nov 2026), not on whichever symbol the broker lists first.
  return (stored && symbols.includes(stored) ? stored : niftyFirst(symbols));
}
type PositionAudit = { signalTime?: string; signalPrice?: number; macd?: number; fillLatencyMs?: number; message?: string; intrabar?: boolean };
type PositionFocus = { position: { symbol: string; entryTime: string; entryPrice: number; audit?: PositionAudit }; list: { symbol: string; entryTime: string; entryPrice: number }[]; index: number };

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [selected, setSelected] = useState("");
  const [ticks, setTicks] = useState<Record<string, Tick>>({});
  const [current, setCurrent] = useState<Record<string, Candle>>({});
  const [indicators, setIndicators] = useState<Record<string, Indicator[]>>({});
  const [signals, setSignals] = useState<Signal[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [smartPortfolios, setSmartPortfolios] = useState<Record<string, Portfolio>>({});
  const [portfolio, setPortfolio] = useState<Portfolio>(emptyPortfolio);
  const [researchTrades, setResearchTrades] = useState<ResearchTrade[]>([]);
  const [researchSummary, setResearchSummary] = useState<ResearchSummary>();
  const [researchOpenPositions, setResearchOpenPositions] = useState<ResearchOpenPosition[]>([]);
  const [researchEquity, setResearchEquity] = useState<EquityPoint[]>([]);
  const [snapshotRevision, setSnapshotRevision] = useState(0);
  const [loadStatus, setLoadStatus] = useState<Record<string, string>>({Orders: "Loading…", Trades: "Loading…", "Research report": "Loading…", "Research trades": "Loading…"});
  const [healthError, setHealthError] = useState("");
  const [healthUpdated, setHealthUpdated] = useState<string>();
  // Quote age must advance even while the health request or feed has stopped.
  const [quoteNow, setQuoteNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setQuoteNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const [researchParameters, setResearchParameters] = useState<Record<string, unknown>>();
  const [health, setHealth] = useState<HealthInfo>();
  // Top navigation is by lane; each lane keeps its own views inside it.
  // The open page lives in the URL hash (#/MACD/MARKET/HEATMAP) so a browser refresh reopens it.
  const [route] = useState(readRoute);
  // Market breadth was a MACD view until 4 Nov 2026; old #/MACD/MARKET/... links still open it.
  const [lane, setLane] = useState<Lane>(() => route[0] === "MACD" && route[1] === "MARKET" ? "MARKET" : pick(LANES, route[0], "MACD"));
  const [macdView, setMacdView] = useState<MacdView>(() => pick(MACD_VIEWS, route[0] === "MACD" ? route[1] : undefined, "TERMINAL"));
  const [auctionView, setAuctionView] = useState<"AUCTION" | "OPTIONS" | "PROFILE" | "WORKSTATION">(() => pick(AUCTION_VIEWS, route[0] === "AUCTION" ? route[1] : undefined, "AUCTION"));
  const page = lane === "MACD" ? macdView : lane === "AUCTION" ? auctionView : lane;
  // Grouped pages: each keeps its own full page under a sub-tab bar.
  const [performanceView, setPerformanceView] = useState<"STATISTICS" | "EQUITY">(() => pick(PERFORMANCE_VIEWS, route[1] === "PERFORMANCE" ? route[2] : undefined, "STATISTICS"));
  const [marketView, setMarketView] = useState<"RRG" | "DAYHIGH" | "DISPERSION" | "RATIOS" | "HEATMAP" | "WHEEL" | "COIL" | "TREND" | "STRADDLE">(() => pick(MARKET_VIEWS, route[0] === "MARKET" ? route[1] : route[1] === "MARKET" ? route[2] : undefined, "RRG"));
  useEffect(() => {
    const parts: string[] = [lane];
    if (lane === "MACD") {
      parts.push(macdView);
      if (macdView === "PERFORMANCE") parts.push(performanceView);
    } else if (lane === "AUCTION") parts.push(auctionView);
    else if (lane === "MARKET") parts.push(marketView);
    const query = ["VANGUARD", "CBE"].includes(lane) && window.location.hash.split("?")[0] === `#/${lane}` ? window.location.hash.split("?")[1] : "";
    const hash = `#/${parts.join("/")}${query ? "?" + query : ""}`;
    if (window.location.hash !== hash) { window.history.replaceState(null, "", hash); window.dispatchEvent(new HashChangeEvent("hashchange")); }
  }, [lane, macdView, auctionView, performanceView, marketView]);
  useEffect(() => {
    // Manual edits of the hash (or a pasted link) switch the page without a reload.
    const onHash = () => {
      const next = readRoute();
      const oldMarket = next[0] === "MACD" && next[1] === "MARKET";
      setLane(oldMarket ? "MARKET" : pick(LANES, next[0], "MACD"));
      if (next[0] === "MACD") setMacdView(pick(MACD_VIEWS, next[1], "TERMINAL"));
      if (next[0] === "AUCTION") setAuctionView(pick(AUCTION_VIEWS, next[1], "AUCTION"));
      if (next[1] === "PERFORMANCE") setPerformanceView(pick(PERFORMANCE_VIEWS, next[2], "STATISTICS"));
      if (next[0] === "MARKET" || oldMarket) setMarketView(pick(MARKET_VIEWS, oldMarket ? next[2] : next[1], "RRG"));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  // Chart pages need the height: the six-card summary collapses to one line
  // there unless the viewer chose otherwise (remembered per browser).
  const [summaryPref, setSummaryPref] = useState<"compact" | "full" | null>(() => {
    try { const v = window.localStorage.getItem("macd.summary"); return v === "compact" || v === "full" ? v : null; } catch { return null; }
  });
  const summaryCompact = summaryPref ? summaryPref === "compact" : page === "PROFILE" || page === "AUCTION" || page === "QUANT" || page === "AGENT";
  const toggleSummary = () => {
    const next = summaryCompact ? "full" : "compact";
    setSummaryPref(next);
    try { window.localStorage.setItem("macd.summary", next); } catch { /* per-viewer convenience only */ }
  };
  // Pages size themselves to what is left below the header stack, measured,
  // not assumed: a fixed 152px left the Profile/Flow chart squeezed and the
  // page overflowing whenever the header wrapped or the summary was taller.
  const chromeRef = useRef<HTMLDivElement>(null);
  const [chromeHeight, setChromeHeight] = useState(152);
  useEffect(() => {
    const el = chromeRef.current;
    if (!el) return;
    const measure = () => setChromeHeight(Math.round(el.getBoundingClientRect().height));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"FYERS" | "ALERTS" | "STRATEGY">("FYERS");
  const openSettings = (tab: "FYERS" | "ALERTS" | "STRATEGY") => { setSettingsTab(tab); setSettingsOpen(true); };
  const [watchSearch, setWatchSearch] = useState("");
  const [watchTab, setWatchTab] = useState<WatchTab>("SPOT");
  const [watchSort, setWatchSort] = useState<{ key: WatchSortKey; direction: "asc" | "desc" }>({ key: "instrument", direction: "asc" });
  const [chartPeriod, setChartPeriod] = useState<ChartPeriod>(() => {
    const stored = readKey(CHART_PERIOD_STORE) as ChartPeriod | null;
    return stored && CHART_PERIODS.some((row) => row.key === stored) ? stored : "5D";
  });
  const pickChartPeriod = (key: ChartPeriod) => { setChartPeriod(key); writeKey(CHART_PERIOD_STORE, key); };
  const [chartFrame, setChartFrame] = useState<number | null>(() => {
    const stored = Number(readKey(CHART_FRAME_STORE));
    return CHART_FRAMES.some(([seconds]) => seconds === stored) ? stored : null;
  });
  const pickChartFrame = (seconds: number, lane: number | undefined) => {
    const next = seconds === lane ? null : seconds;          // null: follow the lane's frame if it is reconfigured
    setChartFrame(next); writeKey(CHART_FRAME_STORE, next === null ? "" : String(next));
  };
  const [fullChart, setFullChart] = useState<{ symbol: string; timeframe: number; candles: Candle[]; indicators: Indicator[] }>();
  const [fullChartError, setFullChartError] = useState("");
  const [chartLoading, setChartLoading] = useState(false);
  const [positionFocus, setPositionFocus] = useState<PositionFocus>();
  const [chartLayers, setChartLayers] = useState<ChartLayers>(() => readStore(CHART_LAYERS_STORE, DEFAULT_LAYERS));
  const toggleLayer = useCallback((key: keyof ChartLayers) => setChartLayers((old) => { const next = { ...old, [key]: !old[key] }; writeKey(CHART_LAYERS_STORE, JSON.stringify(next)); return next; }), []);
  const [paneCollapse, setPaneCollapse] = useState<PaneCollapse>(() => readStore(CHART_PANES_STORE, DEFAULT_PANES));
  const togglePane = useCallback((key: keyof PaneCollapse) => setPaneCollapse((old) => { const next = { ...old, [key]: !old[key] }; writeKey(CHART_PANES_STORE, JSON.stringify(next)); return next; }), []);
  const [priceStyle, setPriceStyle] = useState<PriceStyle>(() => readKey(CHART_STYLE_STORE) === "ohlc" ? "ohlc" : "candles");
  const pickPriceStyle = (style: PriceStyle) => { setPriceStyle(style); writeKey(CHART_STYLE_STORE, style); };
  // Bumping this re-fits the chart without touching symbol or period (key "f").
  const [fitNonce, setFitNonce] = useState(0);
  // Bars that closed since /api/chart was fetched.  The engine publishes only
  // the forming bar (engine.py "candle" event), so the previous forming bar
  // must be retained here when a newer one arrives or it vanishes from the
  // price pane on rollover while the indicator panes keep it.
  const [closedLive, setClosedLive] = useState<Record<string, Candle[]>>({});
  const lastCurrent = useRef<Record<string, Candle>>({});
  const orderVersion = useRef(0);
  const recentOrders = useRef(new Map<string, { version: number; row: Order }>());
  // The blast lane's own state. Its frames are all named blast_*, so nothing
  // here can reach the MACD lane's orders, trades or portfolio above.
  const [blast, setBlast] = useState<BlastSnapshot>();
  const [blastLive, setBlastLive] = useState<BlastCandidate[]>([]);
  const [blastOrders, setBlastOrders] = useState<Order[]>([]);
  const [blastTrades, setBlastTrades] = useState<Trade[]>([]);

  const onEvent = useCallback((event: StreamEvent) => {
    if (event.type === "snapshot") {
      const next = event.data as Snapshot;
      setSnapshot(next); setSelected((value) => next.broker.symbols.includes(value) ? value : restoreSelected(next.broker.symbols || []));
      setSnapshotRevision((n) => n + 1);
      setCurrent({}); setClosedLive({}); lastCurrent.current = {};
      setSignals(next.strategy.signals || []);
      setOrders((old) => mergeRows(old, next.execution.orders || [], "order_id"));
      setTrades((old) => mergeRows(old, next.execution.trades || [], "trade_id"));
      setPortfolio(next.execution.portfolio || emptyPortfolio);
      // The snapshot's journal summary already counts every candidate so far,
      // so the live list restarts from here rather than double counting.
      setBlast(next.blast); setBlastLive([]);
      setIndicators(next.strategy.indicator_history || {});
      const initialTicks: Record<string, Tick> = {};
      // The spot and option lists already carry a tick per row; `watchlist`
      // now holds only the symbols neither of them mentions, so the snapshot
      // no longer ships every quote twice.
      [next.spot_watchlist, next.option_watchlist, next.watchlist].forEach((rows) =>
        rows.forEach((row) => { if (row.tick) initialTicks[row.symbol] = row.tick; }));
      setTicks(initialTicks);
      return;
    }
    if (event.type === "broker") { const status = event.data as Snapshot["broker"]; setSnapshot((old) => old ? { ...old, broker: status } : old); }
    if (event.type === "tick") { const row = event.data as Tick; setTicks((old) => ({ ...old, [row.symbol]: row })); }
    if (event.type === "tick_batch") { const rows = event.data as Tick[]; setTicks((old) => {
      const next = { ...old }; rows.forEach((row) => { next[row.symbol] = row; }); return next;
    }); }
    if (event.type === "candle_batch") {
      const rows = event.data as Candle[];
      setClosedLive((old) => {
        const next = { ...old };
        rows.forEach((row) => { next[row.symbol] = mergeLiveCandles(next[row.symbol] || [], [row]).slice(-500); });
        return next;
      });
    }
    if (event.type === "candle") {
      const row = event.data as Candle;
      const previous = lastCurrent.current[row.symbol];
      lastCurrent.current[row.symbol] = row;
      if (previous && previous.timestamp < row.timestamp) {
        setClosedLive((old) => ({ ...old, [row.symbol]: [...(old[row.symbol] || []).filter((c) => c.timestamp !== previous.timestamp), { ...previous, closed: true }].slice(-500) }));
      }
      setCurrent((old) => ({ ...old, [row.symbol]: row }));
    }
    if (event.type === "indicator") { const row = event.data as Indicator; setIndicators((old) => ({ ...old, [row.symbol]: [...(old[row.symbol] || []).filter((p) => p.timestamp !== row.timestamp), row].slice(-500) })); }
    if (event.type === "signal") setSignals((old) => [event.data as Signal, ...old].slice(0, 200));
    if (event.type === "order") {
      const row = event.data as Order;
      recentOrders.current.set(row.order_id, { version: ++orderVersion.current, row });
      setOrders((old) => mergeRows(old, [row], "order_id"));
    }
    if (event.type === "trade") setTrades((old) => mergeRows(old, [event.data as Trade], "trade_id"));
    // A frame from an older backend without closed_positions still yields a complete Portfolio.
    if (/^(smart_(1l|50l)|spot_macd)_portfolio$/.test(event.type)) {
      const lane = event.type.replace(/_portfolio$/, "");
      setSmartPortfolios(old => ({...old, [lane]: {...event.data as Portfolio, __receivedAt: Date.now()} as Portfolio}));
    }
    if (event.type === "portfolio") setPortfolio({ ...emptyPortfolio, ...(event.data as Portfolio) });
    if (event.type === "blast_candidate") {
      const row = event.data as BlastCandidate;
      setBlastLive((old) => [row, ...old.filter((item) => item.id !== row.id)].slice(0, 500));
    }
    if (event.type === "blast_portfolio") {
      const next = event.data as Portfolio;
      setBlast((old) => old ? { ...old, portfolio: { ...emptyPortfolio, ...next }, closed_positions: next.closed_positions ?? old.closed_positions } : old);
    }
    if (event.type === "blast_order") setBlastOrders((old) => mergeRows(old, [event.data as Order], "order_id"));
    if (event.type === "blast_trade") setBlastTrades((old) => mergeRows(old, [event.data as Trade], "trade_id"));
  }, []);
  const connected = useStream(onEvent);
  const ledgerReady = loadStatus.Orders === "" && loadStatus.Trades === "" && orders.length < 100_000 && trades.length < 100_000;
  const dispersion = useDispersionSeries(snapshot?.option_watchlist || [], indicators, snapshot?.config.timeframe_seconds || 1800);

  useEffect(() => {
    if (!snapshotRevision) return;
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const jobs: [string, string, (data: any, version: number) => void][] = [
      ["Orders", "/api/orders?limit=100000&collapse=true", (rows: Order[], version) => setOrders((old) => mergeRows(mergeRows(old, rows, "order_id"), [...recentOrders.current.values()].filter((entry) => entry.version > version).map((entry) => entry.row), "order_id"))],
      ["Trades", "/api/trades?limit=100000", (rows: Trade[]) => setTrades((old) => mergeRows(rows, old, "trade_id"))],
      ["Research trades", "/api/research/trades?limit=5000", (rows: ResearchTrade[]) => setResearchTrades(rows)],
      ["Research report", "/api/research/report", (report) => {
        setResearchSummary(report?.summary); setResearchParameters(report?.parameters);
        setResearchOpenPositions(report?.open_positions || []); setResearchEquity(report?.equity_curve || []);
      }],
    ];
    const load = async ([name, path, apply]: typeof jobs[number]) => {
      setLoadStatus((old) => ({ ...old, [name]: "Loading…" }));
      const version = orderVersion.current;
      try {
        const data = await requestJson(`${API_URL}${path}`, { headers, signal: controller.signal });
        if (controller.signal.aborted) return;
        apply(data, version);
        setLoadStatus((old) => ({ ...old, [name]: "" }));
      } catch (error) {
        if (controller.signal.aborted) return;
        // A fresh parallel runtime has no archived walk-forward run yet.
        // The engine reports that expected absence as 404; it should not
        // look like stale trading data or retry forever in the global banner.
        if (name.startsWith("Research") && error instanceof Error && error.message.includes("HTTP 404")) {
          setLoadStatus((old) => ({ ...old, [name]: "" }));
          return;
        }
        setLoadStatus((old) => ({ ...old, [name]: `${error instanceof Error ? error.message : "Unavailable"}; displayed data may be incomplete or stale. Retrying…` }));
        timers.push(setTimeout(() => void load([name, path, apply]), 15_000));
      }
    };
    jobs.forEach((job) => void load(job));
    return () => { controller.abort(); timers.forEach(clearTimeout); };
  }, [snapshotRevision]);

  useEffect(() => {
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    let stopped = false;
    let pending = false;
    const controller = new AbortController();
    const poll = () => {
      if (pending) return;
      pending = true;
      void requestJson<HealthInfo>(`${API_URL}/api/system/health`, { headers, signal: controller.signal })
        .then((data) => { if (!stopped) { setHealth(data); setHealthError(""); setHealthUpdated(new Date().toISOString()); } })
        .catch((error) => { if (!stopped) setHealthError(error instanceof Error ? error.message : "Health unavailable"); })
        .finally(() => { pending = false; });
    };
    poll();
    const timer = window.setInterval(poll, 10_000);
    return () => { stopped = true; controller.abort(); clearInterval(timer); };
  }, []);


  const laneFrame = snapshot?.config.timeframe_seconds;
  const frame = chartFrame ?? laneFrame;
  const nativeFrame = frame === laneFrame;
  useEffect(() => {
    if (!selected || !frame) return;
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    // Never combine the previous symbol's full history (or the short live
    // snapshot) with indicators for this symbol.  That was the source of
    // different time domains between the price and indicator panels.
    setFullChart(undefined);
    setFullChartError("");
    setChartLoading(true);
    const load = (quiet: boolean) => fetch(`${API_URL}/api/chart/${encodeURIComponent(selected)}?timeframe_seconds=${frame}`, { headers, signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).detail || "Historical chart request failed");
        return response.json();
      })
      .then((data) => setFullChart({ symbol: selected, timeframe: frame, candles: data.candles, indicators: data.indicators }))
      .catch((error) => { if (error.name !== "AbortError" && !quiet) setFullChartError(error instanceof Error ? error.message : "Historical chart request failed"); })
      .finally(() => { if (!controller.signal.aborted && !quiet) setChartLoading(false); });
    void load(false);
    // Only the lane's frame has live bars on the stream; another frame refreshes from the stored minutes.
    const refresh = nativeFrame ? 0 : window.setInterval(() => void load(true), 30_000);
    return () => { controller.abort(); if (refresh) window.clearInterval(refresh); };
  }, [selected, frame, nativeFrame, snapshotRevision]);

  const chartReady = fullChart?.symbol === selected && fullChart.timeframe === frame;
  const historical = useMemo(() => (chartReady ? (nativeFrame ? mergeLiveCandles(fullChart.candles, closedLive[selected] || []) : fullChart.candles) : []), [chartReady, nativeFrame, fullChart, closedLive, selected]);
  const indicatorRows = useMemo(() => {
    if (!chartReady) return [];
    const stored = fullChart.indicators;
    const merged = new Map(stored.map((row) => [row.timestamp, row]));
    // The streaming snapshot is deliberately short.  It may only append a
    // newer point; it must never replace a historical row or become a second
    // time domain for the lower panes.
    const lastStoredTimestamp = stored.length ? stored[stored.length - 1].timestamp : 0;
    if (nativeFrame) (indicators[selected] || []).filter((row) => row.timestamp > lastStoredTimestamp).forEach((row) => merged.set(row.timestamp, row));
    return [...merged.values()].sort((a, b) => a.timestamp - b.timestamp);
  }, [chartReady, nativeFrame, fullChart, indicators, selected]);
  const chartCutoff = useMemo(() => {
    const sessions = CHART_PERIODS.find((row) => row.key === chartPeriod)?.sessions || 0;
    return sessionCutoff(historical, sessions);
  }, [chartPeriod, historical]);
  const visibleCandles = useMemo(() => chartCutoff ? historical.filter((row) => row.timestamp >= chartCutoff) : historical, [historical, chartCutoff]);
  const visibleIndicators = useMemo(() => chartCutoff ? indicatorRows.filter((row) => row.timestamp >= chartCutoff) : indicatorRows, [indicatorRows, chartCutoff]);
  const studyRows = useMemo(() => closeStudies(historical), [historical]);
  const visibleStudies = useMemo(() => chartCutoff ? studyRows.filter((row) => row.timestamp >= chartCutoff) : studyRows, [studyRows, chartCutoff]);
  // Retained bars belong to one timeframe; after a reconfigure they would
  // append 30-minute bars to a 5-minute history.
  useEffect(() => { setClosedLive({}); lastCurrent.current = {}; }, [snapshot?.config.timeframe_seconds]);
  // References are volume-profile levels of the SPOT; an option's premium is
  // a different price domain, so only a spot symbol gets them.
  const referenceSymbol = useMemo(() => (snapshot?.spot_watchlist.some((row) => row.symbol === selected) ? selected : undefined), [snapshot, selected]);
  const chartReferences = useChartReferences(referenceSymbol);
  useEffect(() => { if (selected) writeKey(SELECTED_SYMBOL_STORE, selected); }, [selected]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
      if (page !== "TERMINAL" && !positionFocus) return;
      const index = Number(event.key) - 1;
      if (event.key >= "1" && event.key <= "5" && CHART_PERIODS[index]) { pickChartPeriod(CHART_PERIODS[index].key); return; }
      const key = event.key.toLowerCase();
      if (key === "f") setFitNonce((n) => n + 1);
      else if (key === "v") toggleLayer("volume");
      else if (key === "l") toggleLayer("legend");
      else if (key === "p") toggleLayer("priorDay");
      else if (key === "w") toggleLayer("week");
      else if (key === "n") toggleLayer("nakedPocs");
      else if (key === "i") toggleLayer("ib");
      else if (key === "s") toggleLayer("sessions");
      else if (key === "r") togglePane("rsi");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, positionFocus, toggleLayer, togglePane]);
  // Candidates the blast screen has judged today: the snapshot's count plus what streamed in since.
  const blastBadge = useMemo(() => {
    if (!blast) return "";
    const today = IST_DAY.format(new Date());
    const counted = blast.journal_summary.day === today ? blast.journal_summary.evaluated : 0;
    return counted + blastLive.filter((row) => row.day === today).length || "";
  }, [blast, blastLive]);
  const dayPnl = useMemo(() => {
    const baseline = health?.day_baseline_equity;
    if (baseline === undefined || baseline === null || !portfolio.equity) return undefined;
    return portfolio.equity - baseline;
  }, [health?.day_baseline_equity, portfolio.equity]);
  const todayActivity = useMemo(() => {
    const dayKey = IST_DAY.format(new Date());
    const isToday = (value: string) => IST_DAY.format(new Date(value)) === dayKey;
    return {
      fills: trades.filter((row) => isToday(row.timestamp)).length,
      orders: orders.filter((row) => isToday(row.created_at)).length,
      signals: signals.filter((row) => isToday(row.timestamp as unknown as string)).length,
    };
  }, [trades, orders, signals]);
  const watchRows = useMemo(() => {
    // `ticks` is replaced 10x/s, so this sort ran 10x/s on every page — 1.5ms
    // a pass to order rows that only the terminal renders.
    if (page !== "TERMINAL") return [];
    const rows: (SpotWatchRow | OptionWatchRow)[] = watchTab === "SPOT" ? snapshot?.spot_watchlist || [] : optionRowsForTab(snapshot?.option_watchlist || [], watchTab);
    const direction = watchSort.direction === "asc" ? 1 : -1;
    return rows.filter((row) => matchesWatchSearch(row, watchSearch)).sort((a, b) => compareWatch(a, b, watchSort.key, ticks, indicators) * direction);
  }, [page, watchTab, watchSearch, snapshot, watchSort, ticks, indicators]);
  const watchGroups = useMemo(() => {
    const isIndex = (row: SpotWatchRow | OptionWatchRow) => "underlying" in row ? INDEX_UNDERLYINGS.has(row.underlying) : row.symbol.endsWith("-INDEX");
    return [
      { label: watchTab === "SPOT" ? "Indices" : "Index options", rows: watchRows.filter(isIndex) },
      { label: watchTab === "SPOT" ? "Stocks" : "Stock options", rows: watchRows.filter((row) => !isIndex(row)) },
    ].filter((group) => group.rows.length);
  }, [watchRows, watchTab]);
  // The terminal chart of a held symbol shows that position's fills and working prices.
  const heldPosition = useMemo(() => portfolio.positions.find((row) => row.symbol === selected), [portfolio.positions, selected]);
  const terminalMarkers = useMemo(() => (heldPosition ? fillMarkers(trades, heldPosition.symbol, heldPosition.opened_at) : []), [heldPosition, trades]);
  const terminalLevels = useMemo(() => positionLevels(heldPosition), [heldPosition]);
  const positionMarkers = useMemo<ChartMarker[]>(() => {
    if (!positionFocus) return terminalMarkers;
    if (positionFocus.position.symbol !== selected) return [];
    const { position } = positionFocus;
    // Every fill of this position (entry, adds, staged exits, the exit); the bare ENTRY mark only when none is held.
    const fills = fillMarkers([...trades, ...blastTrades], position.symbol, position.entryTime);
    return [
      ...(fills.length ? fills : [{ timestamp: Math.floor(Date.parse(position.entryTime) / 1000), price: position.entryPrice, label: "ENTRY", tone: "entry" as const }]),
      ...(position.audit?.signalTime && position.audit.signalPrice !== undefined ? [{ timestamp: Math.floor(Date.parse(position.audit.signalTime) / 1000), price: position.audit.signalPrice, label: `${position.audit.intrabar ? "INTRABAR ZERO" : "ZERO"} ${position.audit.macd?.toFixed(4) ?? ""}`, tone: "signal" as const }] : []),
    ];
  }, [positionFocus, selected, trades, blastTrades, terminalMarkers]);
  const positionChartLevels = useMemo(() => {
    if (!positionFocus) return [];
    const { symbol, entryTime } = positionFocus.position;
    const open = [...portfolio.positions, ...(blast?.portfolio.positions ?? [])]
      .find((row) => row.symbol === symbol && Math.abs(Date.parse(row.opened_at) - Date.parse(entryTime)) < 60_000);
    return positionLevels(open);
  }, [positionFocus, portfolio.positions, blast]);

  const auditPosition = useCallback((position: { symbol: string; entryTime: string; entryPrice: number }): PositionAudit | undefined => {
    const entryTime = Date.parse(position.entryTime);
    const order = [...orders]
      .filter((row) => row.symbol === position.symbol && row.side === "BUY" && row.fill_price !== undefined)
      .sort((a, b) => Math.abs(Date.parse(a.created_at) - entryTime) - Math.abs(Date.parse(b.created_at) - entryTime))[0];
    if (!order || Math.abs(Date.parse(order.created_at) - entryTime) > 5_000) return undefined;
    const signal = signals.find((row) => row.signal_id === order.signal_id);
    if (!signal) return { message: "Signal audit unavailable for this legacy fill" };
    if (!signal.evaluated_candle_timestamp) {
      return { message: "Legacy fill: its evaluated candle was not recorded, so the chart zero-cross marker is withheld" };
    }
    const intrabar = signal.kind.includes("INTRABAR");
    return {
      signalTime: new Date(signal.evaluated_candle_timestamp * 1000).toISOString(),
      signalPrice: signal.price,
      macd: signal.macd,
      fillLatencyMs: Math.max(0, Date.parse(order.created_at) - Date.parse(signal.timestamp as unknown as string)),
      intrabar,
      message: intrabar ? "Intrabar MACD zero-cross" : undefined,
    };
  }, [orders, signals]);

  const openPositionChart = useCallback((position: PositionFocus["position"], list: PositionFocus["list"]) => {
    const index = list.findIndex((row) => row.symbol === position.symbol && row.entryTime === position.entryTime);
    setPositionFocus({ position: { ...position, audit: auditPosition(position) }, list, index: Math.max(0, index) });
    setSelected(position.symbol);
  }, [auditPosition]);
  const movePositionFocus = useCallback((direction: -1 | 1) => {
    setPositionFocus((old) => {
      if (!old) return old;
      const index = (old.index + direction + old.list.length) % old.list.length;
      const position = old.list[index];
      setSelected(position.symbol);
      return { ...old, index, position: { ...position, audit: auditPosition(position) } };
    });
  }, [auditPosition]);

  // An inline arrow here would defeat WatchRow's memo: every row would get a
  // new onSelect on every tick commit and re-render regardless.
  const selectWatchRow = useCallback((symbol: string) => {
    setPositionFocus(undefined);
    setSelected(symbol);
  }, []);

  function sortWatch(key: WatchSortKey) {
    setWatchSort((old) => ({ key, direction: old.key === key && old.direction === "asc" ? "desc" : "asc" }));
  }

  // A dead broker feed puts a word in the bar, and the strip has to pay for it
  // out of the percentage rather than out of clipped glyphs.
  const brokerStatus = observedBrokerStatus(snapshot?.broker.status, health, healthUpdated);
  const brokerDown = !!snapshot && brokerStatus !== "connected";
  const token = tokenLeft(snapshot?.broker.token_expires_at, new Date(quoteNow));
  const lastHistoryBar = visibleCandles[visibleCandles.length - 1];
  const formingBar = chartReady ? current[selected] : undefined;
  const latestChartBar = formingBar && (!lastHistoryBar || formingBar.timestamp >= lastHistoryBar.timestamp) ? formingBar : lastHistoryBar;
  const chartSource = chartSourceStatus({
    bar: chartReady ? latestChartBar : undefined,
    quote: quoteStamp(ticks[selected]?.timestamp, quoteNow),
    now: quoteNow,
    marketOpen: health?.session_open,
    streamConnected: connected && brokerStatus === "connected",
    timeframeSeconds: frame,
    loading: chartLoading,
  });
  return <main style={{ "--chrome-h": `${chromeHeight}px` } as CSSProperties}>
    <div className="shell-top" ref={chromeRef}>
    <header className={brokerDown ? "feed-alarm" : undefined}>
      <div className="brand"><span className="logo">M</span><strong>MACD Trader</strong><span className="parallel-badge" title="Parallel React terminal through the Go API gateway">PARALLEL</span></div>
      <div className="index-strip">{HEADER_INDICES.map((row) => <IndexTicker key={row.symbol} label={row.label} short={row.short} tick={ticks[row.symbol]} observedAt={quoteNow} />)}</div>
      <div className="status">
        <SessionChip now={quoteNow} holidays={snapshot?.config.market_holidays} health={health} />
        {brokerDown || token.expired
          ? <button className="fyers-login-cta" onClick={() => openSettings("FYERS")} title={feedTitle(snapshot, health, brokerStatus)}>
              <span className="feed-dot" aria-hidden="true">●</span>{token.expired ? "Log in to Fyers" : `Fyers ${brokerStatus.replace("_", " ")}`}
            </button>
          : <button className="fyers-chip" onClick={() => openSettings("FYERS")} title={`${feedTitle(snapshot, health, brokerStatus)} · stream ${connected ? "linked" : "reconnecting"} · token ${token.text}`}>
              <span className={`feed-dot${feedTone(brokerStatus, health)}`} aria-hidden="true">●</span>Fyers<small>{connected ? token.text : "reconnecting"}</small>
            </button>}
        <span className="mode" title="Execution mode">{sentenceCase(snapshot?.execution.mode) || "—"}</span>
        <AlertChips health={health} />
        <span className="header-date">{IST_HEADER_DAY.format(new Date())}</span>
        <button className="settings-button settings-glyph" onClick={() => openSettings("FYERS")} aria-label="Settings" title="Settings">⚙</button>
      </div>
    </header>
    <nav className="main-tabs" aria-label="Lanes">{LANES.map(([key, label]) => <button key={key} className={lane === key ? "active" : ""} aria-current={lane === key ? "page" : undefined} onClick={() => setLane(key)}>{label}<span>{key === "MACD" ? portfolio.positions.length || "" : key === "BLAST" ? blastBadge : key === "MARKET" && dispersion.current ? `${dispersion.current.ceAbove}/${dispersion.current.peAbove}` : ""}</span></button>)}<a className="mi-hub-link" href={`${MI_URL}/`} target="_blank" rel="noopener noreferrer" title="Open Market Intelligence: brokers, feeds and shared data">MI hub <span aria-hidden="true">↗</span></a></nav>
    {lane === "MACD" && <SubTabs className="lane-views" value={macdView} options={MACD_VIEWS} onChange={setMacdView}
      titles={{ ORDERS: "Today's order attempts (IST), including filled and rejected orders; the full log is one click away", TRADES: "Today's executed buy and sell fills (IST), including partial exits" }}
      badges={{ PORTFOLIO: portfolio.positions.length || "", ORDERS: loadStatus.Orders === "" ? todayActivity.orders : "…", TRADES: loadStatus.Trades === "" ? todayActivity.fills : "…", SIGNALS: signals.length || "" }} />}
    {lane === "AUCTION" && <SubTabs className="lane-views" value={auctionView} options={AUCTION_VIEWS} onChange={setAuctionView} />}
    {Object.entries(loadStatus).some(([, message]) => message) && <div className="data-status" role="status">
      {Object.entries(loadStatus).filter(([, message]) => message).map(([name, message]) => <span key={name}>{name}: {message}</span>)}
      <button onClick={() => setSnapshotRevision((n) => n + 1)}>Retry data</button>
    </div>}
    {lane === "MACD" && <><section className={summaryCompact ? "metrics compact" : "metrics"}>
      <Metric label="Equity" value={snapshot ? `₹${money.format(portfolio.equity)}` : "—"} />
      <Metric label="Day P&L (vs last close)" value={dayPnl === undefined ? "—" : `₹${money.format(dayPnl)}`} tone={dayPnl} />
      <Metric label="Realized (inception)" value={snapshot ? `₹${money.format(portfolio.realized_pnl)}` : "—"} tone={portfolio.realized_pnl} />
      <Metric label="Unrealized" value={snapshot ? `₹${money.format(portfolio.unrealized_pnl)}` : "—"} tone={portfolio.unrealized_pnl} />
      <Metric label="Open positions" value={snapshot ? `${portfolio.positions.length} · ₹${money.format(portfolio.market_value)}` : "—"} />
      <Metric label="Today's activity" value={`${todayActivity.fills} fills · ${todayActivity.orders} orders · ${todayActivity.signals} signals`} />
      <button type="button" className="metrics-toggle" onClick={toggleSummary}
        title={summaryCompact ? "Show the full summary cards" : "Collapse the summary to one line"}
        aria-pressed={summaryCompact}>{summaryCompact ? "▾" : "▴"}</button>
    </section></>}
    </div>
    {page === "PERFORMANCE" && performanceView === "STATISTICS" && (loadStatus.Orders || loadStatus.Trades) ? <><SubTabs value={performanceView} options={PERFORMANCE_VIEWS} onChange={setPerformanceView} /><section className="panel recovery-panel" role="status">Completed-trade statistics require the full ledger. {loadStatus.Orders || loadStatus.Trades}</section></> : page === "TERMINAL" ? <div className="workspace terminal-workspace">
      <aside className="watchlist panel">
        <div className="panel-title">Market watch <span>{watchRows.length} / {watchTab === "SPOT" ? snapshot?.spot_watchlist.length || 0 : optionRowsForTab(snapshot?.option_watchlist || [], watchTab).length}</span></div>
        <div className="watch-search"><input aria-label="Search market watch" type="search" placeholder="Search symbol, strike or expiry…" value={watchSearch} onChange={(event) => setWatchSearch(event.target.value)} /><button disabled={!watchSearch} onClick={() => setWatchSearch("")}>Clear</button></div>
        <div className="watch-tabs">{WATCH_TABS.map((tab) => <button className={watchTab === tab.key ? "active" : ""} onClick={() => setWatchTab(tab.key)} key={tab.key}>{tab.label}</button>)}</div>
        <div className="watch-head">{(["instrument", "ltp", "change", "macd", "gex", "volume"] as WatchSortKey[]).map((key) => <button key={key} onClick={() => sortWatch(key)}>{key === "ltp" ? "LTP" : key === "macd" ? "MACD" : key === "gex" ? "GEX" : key.charAt(0).toUpperCase() + key.slice(1)}<small>{watchSort.key === key ? (watchSort.direction === "asc" ? "▲" : "▼") : "↕"}</small></button>)}</div>
        {watchGroups.map((group) => <div key={group.label} className="watch-group"><div className="watch-group-title">{group.label}<span>{group.rows.length}</span></div>{group.rows.map((row) => { const points = indicators[row.symbol] || []; return <WatchRow key={row.symbol} row={row} tick={ticks[row.symbol] || row.tick} indicator={points[points.length - 1] || row.indicator} selected={selected === row.symbol} onSelect={selectWatchRow} observedAt={quoteNow} />; })}</div>)}
        {!watchRows.length && <p className="watch-empty" role="status">{watchSearch ? "No matching instruments. Try another symbol or clear the search." : "Waiting for market watch data…"}</p>}
      </aside>
      <section className="chart panel">
        <div className="panel-title"><span>{selected || "Select instrument"}</span>
          <span className="chart-controls">
            <span className="radar-filter chart-style" aria-label="Price chart style">
              <button className={priceStyle === "candles" ? "active" : ""} aria-pressed={priceStyle === "candles"} onClick={() => pickPriceStyle("candles")}>Candles</button>
              <button className={priceStyle === "ohlc" ? "active" : ""} aria-pressed={priceStyle === "ohlc"} onClick={() => pickPriceStyle("ohlc")}>OHLC</button>
            </span>
            <span className="radar-filter chart-layers" aria-label="Chart layers">
              {([["volume", "Vol", "v"], ["legend", "Legend", "l"], ["priorDay", "PD", "p"], ["week", "WK", "w"], ["nakedPocs", "nPOC", "n"], ["ib", "IB", "i"], ["sessions", "Days", "s"]] as [keyof ChartLayers, string, string][]).map(([key, label, hotkey]) => {
                const needsSpot = key === "priorDay" || key === "week" || key === "nakedPocs";
                return <button key={key} className={chartLayers[key] ? "active" : ""} disabled={needsSpot && !referenceSymbol} title={needsSpot && !referenceSymbol ? "Auction references belong to the spot chart, not an option premium" : `${label} (${hotkey})`} onClick={() => toggleLayer(key)}>{label}</button>;
              })}
            </span>
            <span className="radar-filter chart-layers" aria-label="Lower panes">
              <button className={paneCollapse.rsi ? "" : "active"} title="RSI(14) pane (r)" onClick={() => togglePane("rsi")}>RSI</button>
            </span>
            <span className="radar-filter">{CHART_PERIODS.map((row, index) => <button key={row.key} className={chartPeriod === row.key ? "active" : ""} title={`${row.key} (${index + 1})`} onClick={() => pickChartPeriod(row.key)}>{row.key}</button>)}</span>
            <button className="chart-fit" title="Fit all visible bars (f)" onClick={() => setFitNonce((n) => n + 1)}>Fit</button>
            <span className="radar-filter chart-frames" aria-label="Bar size">{CHART_FRAMES.map(([seconds, label]) => <button key={seconds} className={frame === seconds ? "active" : ""} aria-pressed={frame === seconds}
              title={seconds === laneFrame ? `${label} bars: the MACD lane's signal frame (live)` : `${label} bars from the stored minutes (refreshed every 30 s)`} onClick={() => pickChartFrame(seconds, laneFrame)}>{label}{seconds === laneFrame ? <i className="lane-frame-dot" aria-hidden="true" /> : null}</button>)}</span>
          </span>
        </div>
        <TradingChart candles={visibleCandles} current={chartReady && nativeFrame ? current[selected] : undefined} indicators={visibleIndicators} studies={visibleStudies} markers={positionMarkers} levels={terminalLevels} error={fullChartError || snapshot?.history_errors?.[selected]}
          timeframe={frame} layers={chartLayers} references={chartReferences} paneCollapse={paneCollapse} priceStyle={priceStyle} source={chartSource}
          fitKey={`${selected}:${frame}:${chartPeriod}:${chartReady ? "full" : chartLoading ? "loading" : "empty"}:${fitNonce}`} />
      </section>
    </div> : <Suspense fallback={<div className="quant-message panel" role="status">Loading…</div>}>{page === "QUANT" ? <QuantAnalyticsPage symbols={snapshot?.broker.symbols || []} selected={selected} timeframe={snapshot?.config.timeframe_seconds} onSelect={setSelected} /> : page === "PERFORMANCE" ? <><SubTabs value={performanceView} options={PERFORMANCE_VIEWS} onChange={setPerformanceView} />{performanceView === "EQUITY" ? <EquityCurve parameters={researchParameters} snapshot={snapshot} researchStatus={loadStatus["Research report"] || loadStatus["Research trades"]} researchRows={researchEquity} researchTrades={researchTrades} researchOpenPositions={researchOpenPositions} summary={researchSummary} /> : <TradingLedger tab="STATISTICS" orders={orders} trades={trades} portfolio={portfolio} signals={signals} onOpenPosition={openPositionChart} holidays={snapshot?.config.market_holidays} ledgerReady={ledgerReady} reentry={snapshot?.execution.reentry} />}</> : page === "AGENT" ? <AgentPage ticks={ticks} /> : page === "AGENTIC" ? <AgenticPage /> : page === "LANES" ? <LanesPage ticks={ticks} portfolios={smartPortfolios} /> : page === "SCORE" ? <ScoreboardPage /> : page === "SPOT" ? <SpotLanePage ticks={ticks} portfolio={smartPortfolios.spot_macd} holidays={snapshot?.config.market_holidays} /> : page === "UPSTOX" ? <UpstoxLanePage ticks={ticks} holidays={snapshot?.config.market_holidays} /> : page === "VANGUARD" || page === "CBE" ? <NomadLanePage lane={page === "VANGUARD" ? "vanguard" : "cbe"} ticks={ticks} connected={connected} /> : page === "MARKET" ? <><SubTabs value={marketView} options={MARKET_VIEWS} onChange={setMarketView} />{marketView === "RRG" ? <RRGPage /> : marketView === "DAYHIGH" ? <DayHighs onOpen={(symbol) => { setSelected(symbol); setLane("MACD"); setMacdView("TERMINAL"); }} /> : marketView === "TREND" ? <TrendRotation /> : marketView === "HEATMAP" ? <AtmHeatmap /> : marketView === "STRADDLE" ? <StraddlePage /> : marketView === "WHEEL" ? <SectorWheel /> : marketView === "COIL" ? <SectorCoil /> : marketView === "DISPERSION" ? <DispersionPage current={dispersion.current} history={dispersion.history} error={dispersion.error} timeframe={snapshot?.config.timeframe_seconds || 1800} /> : <RatioPage options={snapshot?.option_watchlist || []} />}</> : page === "AUCTION" ? <AuctionPage /> : page === "OPTIONS" ? <OptionsDeskPage /> : page === "PROFILE" ? <MarketProfilePage /> : page === "WORKSTATION" ? <PlatformWorkstation /> : page === "MCX" ? <McxLanePage /> : page === "BLAST" ? <BlastLanePage snapshot={blast} policy={health?.blast?.entry_policy} live={blastLive} liveOrders={blastOrders} liveTrades={blastTrades} revision={snapshotRevision} onSnapshot={setBlast} onOpenPosition={openPositionChart} holidays={snapshot?.config.market_holidays} /> : page === "SIGNALS" ? <SignalRadar signals={signals} /> : page === "STATUS" ? <section className="ledger-page status-page"><div className="page-heading"><div><h1>Status</h1><p>Readiness, portfolio exposure, and book Greeks.</p></div></div><div className="status-stack"><Readiness health={health} error={healthError} updated={healthUpdated} open /><PortfolioRisk portfolio={portfolio} contracts={snapshot?.option_watchlist || []} limits={snapshot?.execution.risk} open /><BookGreeks /><LedgerCounts orders={orders} trades={trades} ready={ledgerReady} /></div></section> : <TradingLedger tab={page as LedgerTab} orders={orders} trades={trades} portfolio={portfolio} signals={signals} onOpenPosition={openPositionChart} holidays={snapshot?.config.market_holidays} ledgerReady={ledgerReady} reentry={snapshot?.execution.reentry} />}</Suspense>}
    {positionFocus && <PositionChartModal focus={positionFocus} levels={positionChartLevels} candles={visibleCandles} current={chartReady && nativeFrame ? current[selected] : undefined} indicators={visibleIndicators} studies={visibleStudies} markers={positionMarkers} error={fullChartError || snapshot?.history_errors?.[selected]} timeframe={frame} period={chartPeriod} layers={chartLayers} references={chartReferences} paneCollapse={paneCollapse} priceStyle={priceStyle} source={chartSource} onPriceStyle={pickPriceStyle} onPeriod={pickChartPeriod} onPrevious={() => movePositionFocus(-1)} onNext={() => movePositionFocus(1)} onClose={() => setPositionFocus(undefined)} />}
    <SettingsPanel open={settingsOpen} initialTab={settingsTab} onClose={() => setSettingsOpen(false)} />
  </main>;
}

function mergeRows<Row extends Record<string, unknown>>(current: Row[], incoming: Row[], key: keyof Row): Row[] {
  const rows = new Map(current.map((row) => [String(row[key]), row]));
  incoming.forEach((row) => rows.set(String(row[key]), row));
  return [...rows.values()];
}

function readRoute(): string[] {
  try { return decodeURIComponent(window.location.hash.split("?")[0]).replace(/^#\/?/, "").split("/").filter(Boolean); } catch { return []; }
}
function pick<T extends string>(options: readonly (readonly [T, string])[], value: string | undefined, fallback: T): T {
  return (options.find(([key]) => key === value)?.[0] ?? fallback) as T;
}
const PERFORMANCE_VIEWS = [["STATISTICS", "Trade statistics"], ["EQUITY", "Equity curve"]] as const;
const MARKET_VIEWS = [["RRG", "RRG"], ["DAYHIGH", "Day highs"], ["TREND", "Trend rotation"], ["DISPERSION", "Dispersion"], ["RATIOS", "Premium ratios"], ["HEATMAP", "ATM heat map"], ["STRADDLE", "ATM straddle & OI"], ["WHEEL", "Sector wheel"], ["COIL", "Sector coil"]] as const;

type Lane = "MACD" | "MARKET" | "SCORE" | "LANES" | "SPOT" | "BLAST" | "MCX" | "AUCTION" | "AGENT" | "AGENTIC" | "VANGUARD" | "CBE" | "UPSTOX";
type MacdView = "TERMINAL" | Exclude<LedgerTab, "STATISTICS"> | "PERFORMANCE" | "QUANT" | "STATUS";
const LANES = [["MACD", "MACD lane"], ["MARKET", "Market breadth"], ["SCORE", "Scoreboard"], ["LANES", "Smart lanes"], ["SPOT", "Spot MACD"], ["BLAST", "Blast lane"], ["MCX", "MCX lane"], ["AUCTION", "Auction desk"], ["AGENT", "Option agent"], ["AGENTIC", "Agentic lane"], ["VANGUARD", "Vanguard"], ["CBE", "CBE"], ["UPSTOX", "Upstox live"]] as const;
const MACD_VIEWS = [["TERMINAL", "Terminal"], ["PORTFOLIO", "Portfolio"], ["STATUS", "Status"], ["ORDERS", "Orders"], ["TRADES", "Trades"], ["PERFORMANCE", "Performance"],
  ["SIGNALS", "Signals"], ["QUANT", "Quant analytics"]] as const;
const AUCTION_VIEWS = [["AUCTION", "Desk"], ["OPTIONS", "Options & flow"], ["PROFILE", "Profile / Flow"], ["WORKSTATION", "Live workstation"]] as const;

/** The view switch inside a lane, and inside a grouped view (Performance, Market breadth). */
function SubTabs<T extends string>({ value, options, onChange, badges, titles, className = "" }: {
  value: T; options: readonly (readonly [T, string])[]; onChange: (view: T) => void;
  badges?: Partial<Record<T, string | number>>; titles?: Partial<Record<T, string>>; className?: string;
}) {
  return <nav className={`sub-tabs ${className}`} aria-label="Views">{options.map(([key, label]) => <button key={key} title={titles?.[key]} className={value === key ? "active" : ""} aria-current={value === key ? "page" : undefined} onClick={() => onChange(key)}>{label}{badges?.[key] !== undefined && badges[key] !== "" && <span>{badges[key]}</span>}</button>)}</nav>;
}


// toLocaleTimeString builds a fresh Intl formatter on every call. Measured
// against a hoisted one: 16.0ms vs 0.98ms to paint 651 watch rows, and the
// tape commits at 10Hz — 160ms/s of main thread spent formatting clocks.
const TRADE_CLOCK = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const IST_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" });
const IST_HEADER_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });


// GEX runs to crores of rupees of dealer gamma per 1% move; abbreviate so the
// column stays one glyph-row wide.
function formatGex(value?: number | null) {
  if (value === undefined || value === null) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}k`;
  return `${sign}${abs.toFixed(0)}`;
}

function gexTone(option?: OptionWatchRow) {
  if (!option || option.gex === undefined || option.gex === null) return "";
  return option.gex >= 0 ? "positive" : "negative";
}

function feedHealth(health?: HealthInfo) {
  if (!health) return "—";
  const age = health.last_tick_age_seconds;
  const ageLabel = age === undefined || age === null ? "no ticks" : age < 3 ? "live" : `${age}s ago`;
  return `${health.tick_rate_per_second}/s · ${ageLabel}`;
}

// The tick-rate badge gives its age suffix up to the index strip; the full
// "12/s · 4s ago" text stays in the badge title.

// The whale badge was the only surface that read health.chain.error and
// health.chain.whale_error; the Auction page renders chain data but not its
// errors.  A stalled chain builder is what froze features_flow for weeks, so
// it now tints the feed dot amber and names itself in the dot's title, which
// costs no header width.
function chainError(health?: HealthInfo) {
  return health?.chain?.error || health?.chain?.whale_error || "";
}

function feedTone(status: string, health?: HealthInfo) {
  if (status !== "connected") return "";
  return chainError(health) ? " degraded" : " online";
}

function feedTitle(snapshot: Snapshot | undefined, health: HealthInfo | undefined, status: string) {
  const parts = [`${snapshot?.config.feed_mode || "—"} · ${status}`];
  if (snapshot?.broker.error && status !== "connected") parts.push(snapshot.broker.error);
  const chain = chainError(health);
  if (chain) parts.push(`chain: ${chain}`);
  return parts.join(" · ");
}


function Metric({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return <div><span>{label}</span><b className={tone === undefined ? "" : tone >= 0 ? "positive" : "negative"}>{value}</b></div>;
}

// Header quotes move at the full 10Hz tick rate; memoising per index stops a
// NIFTY print from re-rendering the BANKNIFTY and SENSEX cells beside it.
const IndexTicker = memo(function IndexTicker({ label, short, tick, observedAt }: { label: string; short: string; tick?: Tick; observedAt: number }) {
  const finite = (value?: number | null) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
  const ltp = finite(tick?.ltp);
  const change = finite(tick?.change ?? (ltp !== undefined && tick?.prev_close ? ltp - tick.prev_close : undefined));
  const pct = finite(tick?.change_pct ?? (change !== undefined && tick?.prev_close ? change / tick.prev_close * 100 : undefined));
  const tone = change === undefined ? "" : change >= 0 ? "positive" : "negative";
  const stamp = quoteStamp(tick?.timestamp, observedAt);
  // A brief flash on each new print, so a moving index reads as moving.
  const previous = useRef<number | undefined>(undefined);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    const before = previous.current;
    previous.current = ltp;
    if (ltp === undefined || before === undefined || ltp === before) return;
    setFlash(ltp > before ? "ix-flash-up" : "ix-flash-down");
    const timer = window.setTimeout(() => setFlash(""), 650);
    return () => window.clearTimeout(timer);
  }, [ltp]);
  return <div className={`ix ${tone} ${flash}${stamp.aged ? " quote-old" : ""}`} title={`${label} · ${stamp.old ? "previous close" : stamp.aged ? "delayed quote" : "live"} · exchange quote ${stamp.title}`}>
    <div className="ix-row">
      <span className="ix-name"><span className="ix-full">{label}</span><span className="ix-short">{short}</span></span>
      {stamp.aged && <span className="ix-aged" aria-label={stamp.old ? "previous close" : "delayed quote"} />}
      {pct !== undefined && <span className={`ix-pill ${tone}`}>{pct >= 0 ? "▲" : "▼"} {Math.abs(pct).toFixed(2)}%</span>}
    </div>
    <div className="ix-row">
      <span className="ix-ltp">{ltp === undefined ? "—" : money.format(ltp)}</span>
      <span className={`ix-chg ${tone}`}>{change === undefined ? "" : `${change >= 0 ? "+" : ""}${money.format(change)}`}</span>
    </div>
  </div>;
});

const SessionChip = memo(function SessionChip({ now, holidays, health }: { now: number; holidays?: string[]; health?: HealthInfo }) {
  const state = sessionState(new Date(now), holidays);
  const rate = state.phase === "open" && health?.session_open ? ` · ${health.tick_rate_per_second}/s` : "";
  return <span className={`session-chip session-${state.phase}`} title={health ? `${feedHealth(health)} · ${health.ticks_total ?? 0} ticks · ${health.candles_closed ?? 0} candles closed` : state.label}>
    <i aria-hidden="true" />{state.label}{rate}<small>{state.detail}</small>
  </span>;
});

// Only a fraction of ~650 rows print inside any 100ms commit window; without
// this the whole tab reconciles every time.
const WatchRow = memo(function WatchRow({ row, tick, indicator, selected, onSelect, observedAt }: { observedAt: number; row: SpotWatchRow | OptionWatchRow; tick?: Tick; indicator?: Indicator; selected: boolean; onSelect: (symbol: string) => void }) {
  const option = "option_type" in row ? row : undefined;
  const stamp = quoteStamp(tick?.timestamp, observedAt);
  const change = stamp.untraded ? undefined : tick?.change ?? (tick?.prev_close ? tick.ltp - tick.prev_close : undefined);
  const pct = stamp.untraded ? undefined : tick?.change_pct ?? (change !== undefined && tick?.prev_close ? change / tick.prev_close * 100 : undefined);
  return <button title={`${row.symbol} · Exchange quote: ${stamp.title}`} className={`${selected ? "watch-row active" : "watch-row"}${stamp.aged ? " quote-old" : ""}`} onClick={() => onSelect(row.symbol)}>
    <span><b>{option ? `${option.underlying} ${money.format(option.strike)} ${option.option_type}` : row.symbol.split(":")[1].replace("-EQ", "")}</b><small>{option ? `${option.moneyness || "ATM"} · ${option.expiry} · LOT ${option.lot_size || "—"}` : row.symbol.split(":")[0]}{option?.retained ? " · HELD" : ""}</small></span>
    <span>{tick && !stamp.untraded ? money.format(tick.ltp) : "—"}<small>{stamp.label}</small></span>
    <span className={(change || 0) >= 0 ? "positive" : "negative"}>{change === undefined ? "—" : `${change >= 0 ? "+" : ""}${money.format(change)}`}<small>{pct === undefined ? "" : `${pct.toFixed(2)}%`}</small></span>
    <span className={(indicator?.macd || 0) >= 0 ? "positive" : "negative"}>{indicator ? indicator.macd.toFixed(2) : "—"}</span>
    <span className={gexTone(option)} title={option?.iv != null ? `IV ${option.iv}% · dealer gamma per 1% move` : "GEX needs open interest and a spot mark"}>
      {formatGex(option?.gex)}<small>{option?.iv != null ? `IV ${option.iv}%` : ""}</small></span>
    <span>{tick ? money.format(tick.volume) : "—"}</span>
  </button>;
});

function compareWatch(a: SpotWatchRow | OptionWatchRow, b: SpotWatchRow | OptionWatchRow, key: WatchSortKey, ticks: Record<string, Tick>, indicators: Record<string, Indicator[]>) {
  const value = (row: SpotWatchRow | OptionWatchRow): string | number => {
    const tick = ticks[row.symbol] || row.tick;
    const points = indicators[row.symbol] || [];
    const point = points[points.length - 1] || row.indicator;
    if (key === "instrument") return "underlying" in row ? `${row.underlying} ${row.strike} ${row.option_type}` : row.symbol;
    if (key === "ltp") return tick?.ltp || 0;
    if (key === "change") return tick?.change_pct ?? tick?.change ?? 0;
    if (key === "macd") return point?.macd || 0;
    if (key === "gex") return "gex" in row && typeof row.gex === "number" ? row.gex : 0;
    return tick?.volume || 0;
  };
  const av = value(a); const bv = value(b);
  return typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true });
}

/** Telegram alert health and rejected exits backing off, in the header (review 3 Nov 2026: a 403 went unseen). */
function sentenceCase(text?: string | null) {
  const value = (text ?? "").trim();
  return value ? value[0].toUpperCase() + value.slice(1).toLowerCase() : "";
}

function AlertChips({ health }: { health?: HealthInfo }) {
  const alerts = health?.alerts;
  const exits = health?.exit_failures ?? [];
  if (!alerts && !exits.length) return null;
  const failing = !!alerts?.last_error || !!alerts?.chat_is_bot;
  const text = !alerts ? "" : !alerts.configured ? "Alerts off" : alerts.chat_is_bot ? "Alerts misconfigured" : failing ? "Alerts failing" : alerts.sent_total ? "Alerts ok" : "Alerts idle";
  const title = !alerts ? "" : !alerts.configured ? "Telegram bot token or chat id not set: no alert can be sent"
    : alerts.chat_is_bot ? "The Telegram chat id is a bot (its own id or a @…bot username), and Telegram never lets a bot message a bot (HTTP 403). Set your own numeric chat id in Settings."
    : failing ? `Last Telegram send failed: ${alerts.last_error}` : alerts.sent_total ? `${alerts.sent_total} sent since start · last: ${alerts.last_message ?? ""}`
    : "Telegram configured; nothing has needed an alert since the engine started";
  return <>
    {text && <span className={`mode${failing || !alerts?.configured ? " alert" : ""}`} title={title}>{text}</span>}
    {exits.length > 0 && <span className="mode alert" title={exits.slice(0, 8).map((row) => `${row.lane} ${row.symbol}: ${row.reason ?? "exit"} rejected ${row.count}× since ${row.first_at} — ${row.error}`).join("\n")}>
      {exits.length} exit{exits.length > 1 ? "s" : ""} backing off</span>}
  </>;
}
