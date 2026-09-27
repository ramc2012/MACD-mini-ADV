import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DEFAULT_LAYERS, DEFAULT_PANES, TradingChart } from "./Chart";
import { useChartReferences } from "./useChartReferences";
import { mergeLiveCandles, sessionCutoff } from "./chartMath";
import { PositionChartModal } from "./PositionChartModal";
import { SettingsPanel } from "./SettingsPanel";
import { TradingLedger, type LedgerTab } from "./Ledger";
import { EquityCurve } from "./EquityCurve";
import { RRGPage } from "./RRG";
import { MarketProfilePage } from "./MarketProfile";
import { SignalRadar } from "./SignalRadar";
import { DispersionPage, useDispersionSeries } from "./Dispersion";
import { RatioPage } from "./RatioChart";
import { AuctionPage } from "./Auction";
import { BlastLanePage } from "./BlastLane";
import { QuantAnalyticsPage } from "./QuantAnalytics";
import { API_TOKEN, API_URL } from "./runtime";
import { Readiness } from "./Readiness";
import { PortfolioRisk } from "./PortfolioRisk";
import { matchesWatchSearch, quoteStamp } from "./watchMath";
import { requestJson } from "./requestJson";
import { useStream } from "./useStream";
import { observedBrokerStatus } from "./feedStatus";
import type { BlastCandidate, BlastSnapshot, Candle, ChartLayers, ChartMarker, EquityPoint, HealthInfo, Indicator, OptionWatchRow, Order, PaneCollapse, Portfolio, ResearchOpenPosition, ResearchSummary, ResearchTrade, Signal, Snapshot, SpotWatchRow, StreamEvent, Tick, Trade } from "./types";

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
const HEADER_INDICES: { label: string; symbol: string }[] = [
  { label: "NIFTY", symbol: "NSE:NIFTY50-INDEX" },
  { label: "BANKNIFTY", symbol: "NSE:NIFTYBANK-INDEX" },
  { label: "SENSEX", symbol: "BSE:SENSEX-INDEX" },
];
// A reload used to dump the desk back on the first broker symbol whatever
// it had been charting.  The stored pick is honoured only while the engine
// still subscribes it, so an expired contract cannot strand the chart.
function restoreSelected(symbols: string[]) {
  const stored = readKey(SELECTED_SYMBOL_STORE);
  return (stored && symbols.includes(stored) ? stored : symbols[0]) || "";
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
  const [page, setPage] = useState<"TERMINAL" | "QUANT" | "EQUITY" | "RRG" | "DISPERSION" | "RATIOS" | "AUCTION" | "PROFILE" | "BLAST" | LedgerTab>("TERMINAL");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [watchSearch, setWatchSearch] = useState("");
  const [watchTab, setWatchTab] = useState<"SPOT" | "CE" | "PE">("SPOT");
  const [watchSort, setWatchSort] = useState<{ key: WatchSortKey; direction: "asc" | "desc" }>({ key: "instrument", direction: "asc" });
  const [chartPeriod, setChartPeriod] = useState<ChartPeriod>(() => {
    const stored = readKey(CHART_PERIOD_STORE) as ChartPeriod | null;
    return stored && CHART_PERIODS.some((row) => row.key === stored) ? stored : "5D";
  });
  const pickChartPeriod = (key: ChartPeriod) => { setChartPeriod(key); writeKey(CHART_PERIOD_STORE, key); };
  const [fullChart, setFullChart] = useState<{ symbol: string; timeframe: number; candles: Candle[]; indicators: Indicator[] }>();
  const [fullChartError, setFullChartError] = useState("");
  const [chartLoading, setChartLoading] = useState(false);
  const [positionFocus, setPositionFocus] = useState<PositionFocus>();
  const [chartLayers, setChartLayers] = useState<ChartLayers>(() => readStore(CHART_LAYERS_STORE, DEFAULT_LAYERS));
  const toggleLayer = useCallback((key: keyof ChartLayers) => setChartLayers((old) => { const next = { ...old, [key]: !old[key] }; writeKey(CHART_LAYERS_STORE, JSON.stringify(next)); return next; }), []);
  const [paneCollapse, setPaneCollapse] = useState<PaneCollapse>(() => readStore(CHART_PANES_STORE, DEFAULT_PANES));
  const togglePane = useCallback((key: keyof PaneCollapse) => setPaneCollapse((old) => { const next = { ...old, [key]: !old[key] }; writeKey(CHART_PANES_STORE, JSON.stringify(next)); return next; }), []);
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
  const dispersion = useDispersionSeries(snapshot?.option_watchlist || [], indicators, snapshot?.config.timeframe_seconds || 1800);

  useEffect(() => {
    if (!snapshotRevision) return;
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const jobs: [string, string, (data: any, version: number) => void][] = [
      ["Orders", "/api/orders?limit=100000", (rows: Order[], version) => setOrders((old) => mergeRows(mergeRows(old, rows, "order_id"), [...recentOrders.current.values()].filter((entry) => entry.version > version).map((entry) => entry.row), "order_id"))],
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


  useEffect(() => {
    if (!selected || !snapshot) return;
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    // Never combine the previous symbol's full history (or the short live
    // snapshot) with indicators for this symbol.  That was the source of
    // different time domains between the price and indicator panels.
    setFullChart(undefined);
    setFullChartError("");
    setChartLoading(true);
    void fetch(`${API_URL}/api/chart/${encodeURIComponent(selected)}?timeframe_seconds=${snapshot.config.timeframe_seconds}`, { headers, signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).detail || "Historical chart request failed");
        return response.json();
      })
      .then((data) => setFullChart({ symbol: selected, timeframe: snapshot.config.timeframe_seconds, candles: data.candles, indicators: data.indicators }))
      .catch((error) => { if (error.name !== "AbortError") setFullChartError(error instanceof Error ? error.message : "Historical chart request failed"); })
      .finally(() => { if (!controller.signal.aborted) setChartLoading(false); });
    return () => controller.abort();
  }, [selected, snapshot?.config.timeframe_seconds, snapshotRevision]);

  const chartReady = fullChart?.symbol === selected && fullChart.timeframe === snapshot?.config.timeframe_seconds;
  const historical = useMemo(() => (chartReady ? mergeLiveCandles(fullChart.candles, closedLive[selected] || []) : []), [chartReady, fullChart, closedLive, selected]);
  const indicatorRows = useMemo(() => {
    if (!chartReady) return [];
    const stored = fullChart.indicators;
    const merged = new Map(stored.map((row) => [row.timestamp, row]));
    // The streaming snapshot is deliberately short.  It may only append a
    // newer point; it must never replace a historical row or become a second
    // time domain for the lower panes.
    const lastStoredTimestamp = stored.length ? stored[stored.length - 1].timestamp : 0;
    (indicators[selected] || []).filter((row) => row.timestamp > lastStoredTimestamp).forEach((row) => merged.set(row.timestamp, row));
    return [...merged.values()].sort((a, b) => a.timestamp - b.timestamp);
  }, [chartReady, fullChart, indicators, selected]);
  const chartCutoff = useMemo(() => {
    const sessions = CHART_PERIODS.find((row) => row.key === chartPeriod)?.sessions || 0;
    return sessionCutoff(historical, sessions);
  }, [chartPeriod, historical]);
  const visibleCandles = useMemo(() => chartCutoff ? historical.filter((row) => row.timestamp >= chartCutoff) : historical, [historical, chartCutoff]);
  const visibleIndicators = useMemo(() => chartCutoff ? indicatorRows.filter((row) => row.timestamp >= chartCutoff) : indicatorRows, [indicatorRows, chartCutoff]);
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
      else if (key === "o") togglePane("roc");
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
    const rows: (SpotWatchRow | OptionWatchRow)[] = watchTab === "SPOT" ? snapshot?.spot_watchlist || [] : (snapshot?.option_watchlist || []).filter((row) => row.option_type === watchTab);
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
  const positionMarkers = useMemo<ChartMarker[]>(() => {
    if (positionFocus?.position.symbol !== selected) return [];
    const { position } = positionFocus;
    return [
      { timestamp: Math.floor(Date.parse(position.entryTime) / 1000), price: position.entryPrice, label: "ENTRY", tone: "entry" },
      ...(position.audit?.signalTime && position.audit.signalPrice !== undefined ? [{ timestamp: Math.floor(Date.parse(position.audit.signalTime) / 1000), price: position.audit.signalPrice, label: `${position.audit.intrabar ? "INTRABAR ZERO" : "ZERO"} ${position.audit.macd?.toFixed(4) ?? ""}`, tone: "signal" as const }] : []),
    ];
  }, [positionFocus, selected]);

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
  return <main>
    <header className={brokerDown ? "feed-alarm" : undefined}>
      <div className="brand"><span className="logo">M</span><strong>MACD Trader</strong><span className="parallel-badge" title="Parallel React terminal through the Go API gateway">PARALLEL</span></div>
      <div className="index-strip">{HEADER_INDICES.map((row) => <IndexTicker key={row.symbol} label={row.label} tick={ticks[row.symbol]} observedAt={quoteNow} />)}</div>
      <div className="status">
        <span className={connected ? "dot online" : "dot"} />{connected ? "Linked" : "Reconnecting"}
        <span className={`feed-dot${feedTone(brokerStatus, health)}`} role="img" aria-label={feedTitle(snapshot, health, brokerStatus)} title={feedTitle(snapshot, health, brokerStatus)}>●</span>
        {brokerDown ? <b className="feed-down">{brokerStatus.toUpperCase()}</b> : undefined}
        <span className={healthTone(health) ? "feed-badge" : "mode alert"} title={`${feedHealth(health)} · ${health?.ticks_total ?? 0} ticks · ${health?.candles_closed ?? 0} candles closed · ${(health?.feed_connects ?? 1) - 1} reconnects · ${health?.stream_clients ?? 0} clients`}>{feedRate(health)}</span>
        <span className="mode">{snapshot?.execution.mode || "—"}</span>
        <span className="header-date">{IST_HEADER_DAY.format(new Date())}</span>
        <button className="settings-button settings-glyph" onClick={() => setSettingsOpen(true)} aria-label="Settings" title="Settings">⚙</button>
      </div>
    </header>
    <nav className="main-tabs" aria-label="Main navigation">{(["TERMINAL", "QUANT", "PORTFOLIO", "ORDERS", "TRADES", "STATISTICS", "EQUITY", "RRG", "DISPERSION", "RATIOS", "AUCTION", "PROFILE", "BLAST", "SIGNALS"] as const).map((tab) => <button key={tab} className={page === tab ? "active" : ""} aria-current={page === tab ? "page" : undefined} onClick={() => setPage(tab)}>{tab === "TERMINAL" ? "Trading terminal" : tab === "QUANT" ? "Quant analytics" : tab === "STATISTICS" ? "Trade statistics" : tab === "EQUITY" ? "Equity curve" : tab === "RRG" ? "RRG" : tab === "DISPERSION" ? "Dispersion" : tab === "RATIOS" ? "Premium ratios" : tab === "AUCTION" ? "Auction" : tab === "PROFILE" ? "Profile / Flow" : tab === "BLAST" ? "Blast lane" : tab.charAt(0) + tab.slice(1).toLowerCase()}<span>{tab === "BLAST" ? blastBadge : tab === "EQUITY" ? researchEquity.length : tab === "DISPERSION" ? (dispersion.current ? `${dispersion.current.ceAbove}/${dispersion.current.peAbove}` : "") : navCount(tab, orders, trades, portfolio, signals)}</span></button>)}</nav>
    {Object.entries(loadStatus).some(([, message]) => message) && <div className="data-status" role="status">
      {Object.entries(loadStatus).filter(([, message]) => message).map(([name, message]) => <span key={name}>{name}: {message}</span>)}
      <button onClick={() => setSnapshotRevision((n) => n + 1)}>Retry data</button>
    </div>}
    <section className="metrics">
      <Metric label="Equity" value={snapshot ? `₹${money.format(portfolio.equity)}` : "—"} />
      <Metric label="Day P&L (vs last close)" value={dayPnl === undefined ? "—" : `₹${money.format(dayPnl)}`} tone={dayPnl} />
      <Metric label="Realized (inception)" value={snapshot ? `₹${money.format(portfolio.realized_pnl)}` : "—"} tone={portfolio.realized_pnl} />
      <Metric label="Unrealized" value={snapshot ? `₹${money.format(portfolio.unrealized_pnl)}` : "—"} tone={portfolio.unrealized_pnl} />
      <Metric label="Open positions" value={snapshot ? `${portfolio.positions.length} · ₹${money.format(portfolio.market_value)}` : "—"} />
      <Metric label="Today's activity" value={`${todayActivity.fills} fills · ${todayActivity.orders} orders · ${todayActivity.signals} signals`} />
    </section>
    <Readiness health={health} error={healthError} updated={healthUpdated} />
    {page === "PORTFOLIO" && <PortfolioRisk portfolio={portfolio} contracts={snapshot?.option_watchlist || []} limits={snapshot?.execution.risk} />}
    {page === "STATISTICS" && (loadStatus.Orders || loadStatus.Trades) ? <section className="panel recovery-panel" role="status">Completed-trade statistics require the full ledger. {loadStatus.Orders || loadStatus.Trades}</section> : page === "TERMINAL" ? <div className="workspace terminal-workspace">
      <aside className="watchlist panel">
        <div className="panel-title">Market watch <span>{watchRows.length} / {watchTab === "SPOT" ? snapshot?.spot_watchlist.length || 0 : snapshot?.option_watchlist.filter((r) => r.option_type === watchTab).length || 0}</span></div>
        <div className="watch-search"><input aria-label="Search market watch" type="search" placeholder="Search symbol, strike or expiry…" value={watchSearch} onChange={(event) => setWatchSearch(event.target.value)} /><button disabled={!watchSearch} onClick={() => setWatchSearch("")}>Clear</button></div>
        <div className="watch-tabs">{(["SPOT", "CE", "PE"] as const).map((tab) => <button className={watchTab === tab ? "active" : ""} onClick={() => setWatchTab(tab)} key={tab}>{tab}</button>)}</div>
        <div className="watch-head">{(["instrument", "ltp", "change", "macd", "gex", "volume"] as WatchSortKey[]).map((key) => <button key={key} onClick={() => sortWatch(key)}>{key === "ltp" ? "LTP" : key === "macd" ? "MACD" : key === "gex" ? "GEX" : key.charAt(0).toUpperCase() + key.slice(1)}<small>{watchSort.key === key ? (watchSort.direction === "asc" ? "▲" : "▼") : "↕"}</small></button>)}</div>
        {watchGroups.map((group) => <div key={group.label} className="watch-group"><div className="watch-group-title">{group.label}<span>{group.rows.length}</span></div>{group.rows.map((row) => { const points = indicators[row.symbol] || []; return <WatchRow key={row.symbol} row={row} tick={ticks[row.symbol] || row.tick} indicator={points[points.length - 1] || row.indicator} selected={selected === row.symbol} onSelect={selectWatchRow} observedAt={quoteNow} />; })}</div>)}
        {!watchRows.length && <p className="watch-empty" role="status">{watchSearch ? "No matching instruments. Try another symbol or clear the search." : "Waiting for market watch data…"}</p>}
      </aside>
      <section className="chart panel">
        <div className="panel-title"><span>{selected || "Select instrument"}</span>
          <span className="chart-controls">
            <span className="radar-filter chart-layers" aria-label="Chart layers">
              {([["volume", "Vol", "v"], ["legend", "Legend", "l"], ["priorDay", "PD", "p"], ["week", "WK", "w"], ["nakedPocs", "nPOC", "n"], ["ib", "IB", "i"], ["sessions", "Days", "s"]] as [keyof ChartLayers, string, string][]).map(([key, label, hotkey]) => {
                const needsSpot = key === "priorDay" || key === "week" || key === "nakedPocs";
                return <button key={key} className={chartLayers[key] ? "active" : ""} disabled={needsSpot && !referenceSymbol} title={needsSpot && !referenceSymbol ? "Auction references belong to the spot chart, not an option premium" : `${label} (${hotkey})`} onClick={() => toggleLayer(key)}>{label}</button>;
              })}
            </span>
            <span className="radar-filter chart-layers" aria-label="Lower panes">
              <button className={paneCollapse.rsi ? "" : "active"} title="KAMA RSI pane (r)" onClick={() => togglePane("rsi")}>RSI</button>
              <button className={paneCollapse.roc ? "" : "active"} title="KAMA ROC pane (o)" onClick={() => togglePane("roc")}>ROC</button>
            </span>
            <span className="radar-filter">{CHART_PERIODS.map((row, index) => <button key={row.key} className={chartPeriod === row.key ? "active" : ""} title={`${row.key} (${index + 1})`} onClick={() => pickChartPeriod(row.key)}>{row.key}</button>)}</span>
            <button className="chart-fit" title="Fit all visible bars (f)" onClick={() => setFitNonce((n) => n + 1)}>Fit</button>
            <span>{(snapshot?.config.timeframe_seconds || 0) / 60}m</span>
          </span>
        </div>
        <TradingChart candles={visibleCandles} current={chartReady ? current[selected] : undefined} indicators={visibleIndicators} markers={positionMarkers} error={fullChartError || snapshot?.history_errors?.[selected]} rsiGate={snapshot?.config.kama_rsi?.[1]} rocGate={snapshot?.config.kama_roc?.[1]}
          timeframe={snapshot?.config.timeframe_seconds} layers={chartLayers} references={chartReferences} paneCollapse={paneCollapse}
          fitKey={`${selected}:${snapshot?.config.timeframe_seconds}:${chartPeriod}:${chartReady ? "full" : chartLoading ? "loading" : "empty"}:${fitNonce}`} />
      </section>
    </div> : page === "QUANT" ? <QuantAnalyticsPage symbols={snapshot?.broker.symbols || []} selected={selected} timeframe={snapshot?.config.timeframe_seconds} onSelect={setSelected} /> : page === "EQUITY" ? <EquityCurve parameters={researchParameters} snapshot={snapshot} researchStatus={loadStatus["Research report"] || loadStatus["Research trades"]} researchRows={researchEquity} researchTrades={researchTrades} researchOpenPositions={researchOpenPositions} summary={researchSummary} /> : page === "RRG" ? <RRGPage /> : page === "DISPERSION" ? <DispersionPage current={dispersion.current} history={dispersion.history} error={dispersion.error} timeframe={snapshot?.config.timeframe_seconds || 1800} /> : page === "RATIOS" ? <RatioPage options={snapshot?.option_watchlist || []} /> : page === "AUCTION" ? <AuctionPage /> : page === "PROFILE" ? <MarketProfilePage /> : page === "BLAST" ? <BlastLanePage snapshot={blast} live={blastLive} liveOrders={blastOrders} liveTrades={blastTrades} revision={snapshotRevision} onSnapshot={setBlast} onOpenPosition={openPositionChart} /> : page === "SIGNALS" ? <SignalRadar signals={signals} /> : <TradingLedger tab={page} orders={orders} trades={trades} portfolio={portfolio} signals={signals} onOpenPosition={openPositionChart} holidays={snapshot?.config.market_holidays} />}
    {positionFocus && <PositionChartModal focus={positionFocus} candles={visibleCandles} current={chartReady ? current[selected] : undefined} indicators={visibleIndicators} markers={positionMarkers} error={fullChartError || snapshot?.history_errors?.[selected]} timeframe={snapshot?.config.timeframe_seconds} period={chartPeriod} rsiGate={snapshot?.config.kama_rsi?.[1]} rocGate={snapshot?.config.kama_roc?.[1]} layers={chartLayers} references={chartReferences} paneCollapse={paneCollapse} onPeriod={pickChartPeriod} onPrevious={() => movePositionFocus(-1)} onNext={() => movePositionFocus(1)} onClose={() => setPositionFocus(undefined)} />}
    <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
  </main>;
}

function mergeRows<Row extends Record<string, unknown>>(current: Row[], incoming: Row[], key: keyof Row): Row[] {
  const rows = new Map(current.map((row) => [String(row[key]), row]));
  incoming.forEach((row) => rows.set(String(row[key]), row));
  return [...rows.values()];
}

// STATISTICS carries no badge: it used to show the walk-forward trade count, which the page no longer reports.
function navCount(tab: string, orders: Order[], trades: Trade[], portfolio: Portfolio, signals: Signal[]) {
  return tab === "ORDERS" ? orders.length : tab === "TRADES" ? trades.length : tab === "PORTFOLIO" ? portfolio.positions.length : tab === "SIGNALS" ? signals.length : "";
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
function feedRate(health?: HealthInfo) {
  return health ? health.session_open ? `${health.tick_rate_per_second}/s` : "Closed" : "—";
}

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

function healthTone(health?: HealthInfo) {
  if (!health) return false;
  const age = health.last_tick_age_seconds;
  return health.status === "connected" && age !== undefined && age !== null && age < 120;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return <div><span>{label}</span><b className={tone === undefined ? "" : tone >= 0 ? "positive" : "negative"}>{value}</b></div>;
}

// Header quotes move at the full 10Hz tick rate; memoising per index stops a
// NIFTY print from re-rendering the BANKNIFTY and SENSEX cells beside it.
const IndexTicker = memo(function IndexTicker({ label, tick, observedAt }: { label: string; tick?: Tick; observedAt: number }) {
  const finite = (value?: number | null) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
  const ltp = finite(tick?.ltp);
  const change = finite(tick?.change ?? (ltp !== undefined && tick?.prev_close ? ltp - tick.prev_close : undefined));
  const pct = finite(tick?.change_pct ?? (change !== undefined && tick?.prev_close ? change / tick.prev_close * 100 : undefined));
  const tone = change === undefined ? "" : change >= 0 ? "positive" : "negative";
  const stamp = quoteStamp(tick?.timestamp, observedAt);
  return <span className={`index-tick${stamp.aged ? " quote-old" : ""}`} title={`Exchange quote: ${stamp.title}`}>
    <b>{label}{stamp.aged && <em className="quote-age">{stamp.old ? "prior" : "aged"}</em>}</b>
    <span className={tone}>{ltp === undefined ? "—" : money.format(ltp)}</span>
    <small className={tone}>{change === undefined ? "—" : `${change >= 0 ? "+" : ""}${money.format(change)}`}</small>
    {pct === undefined ? undefined : <i className={`index-pct ${tone}`}>{`${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`}</i>}
  </span>;
});

// Only a fraction of ~650 rows print inside any 100ms commit window; without
// this the whole tab reconciles every time.
const WatchRow = memo(function WatchRow({ row, tick, indicator, selected, onSelect, observedAt }: { observedAt: number; row: SpotWatchRow | OptionWatchRow; tick?: Tick; indicator?: Indicator; selected: boolean; onSelect: (symbol: string) => void }) {
  const option = "option_type" in row ? row : undefined;
  const change = tick?.change ?? (tick?.prev_close ? tick.ltp - tick.prev_close : undefined);
  const pct = tick?.change_pct ?? (change !== undefined && tick?.prev_close ? change / tick.prev_close * 100 : undefined);
  const stamp = quoteStamp(tick?.timestamp, observedAt);
  return <button title={`${row.symbol} · Exchange quote: ${stamp.title}`} className={`${selected ? "watch-row active" : "watch-row"}${stamp.aged ? " quote-old" : ""}`} onClick={() => onSelect(row.symbol)}>
    <span><b>{option ? `${option.underlying} ${money.format(option.strike)} ${option.option_type}` : row.symbol.split(":")[1].replace("-EQ", "")}</b><small>{option ? `${option.moneyness || "ATM"} · ${option.expiry} · LOT ${option.lot_size || "—"}` : row.symbol.split(":")[0]}{option?.retained ? " · HELD" : ""}</small></span>
    <span>{tick ? money.format(tick.ltp) : "—"}<small>{stamp.label}</small></span>
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
