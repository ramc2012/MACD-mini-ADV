import { ColorType, createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { AuctionWorkstation } from "./AuctionWorkstation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import type { AuctionContext, AuctionFlow, BaseRateGroup, BaseRates, ProfileRow, Rate, SetupJournal, WhaleView } from "./types";

/** The auction desk's long memory, made visible.
 *
 * Three questions, in the order a positional read actually asks them:
 * where is price against the day, week and month; is today ordinary for this
 * regime; and do the book and the tape agree about who is pressing.
 */
const CLOCK = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const price = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const TIMEFRAMES = [
  { key: "day", label: "Prior day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
] as const;

const headers = () => (API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined);
const SYMBOL_STORE = "macd.auctionSymbol";
const REGIME_STORE = "macd.auctionRegime";
// Safari's private mode throws on both halves of localStorage, and a rejected
// preference must not take the page down with it.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { return; } };

function useEndpoint<T>(path: string | null, deps: unknown[]) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setLoading(true);
    void fetch(`${API_URL}${path}`, { headers: headers(), signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).detail || `${path} failed`);
        return response.json() as Promise<T>;
      })
      .then((value) => { setData(value); setError(""); })
      .catch((reason) => { if (reason.name !== "AbortError") setError(reason instanceof Error ? reason.message : "request failed"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

type PeriodReading = {
  period?: string; starts?: string; rvol?: number | null; rvol_p90?: number; volume_abnormal?: boolean;
  range_atr?: number; range_usual?: number; range_p90?: number; range_abnormal?: boolean;
  delta_share?: number; delta_p90?: number | null; delta_abnormal?: boolean; day_high_share?: number; day_low_share?: number;
};
type Annotations = { symbol: string; at: string; notes: string[]; period: PeriodReading; error?: string;
  eighty_rule?: { state: string; rule_active?: boolean } };

// Profile notes with the base rate each was measured with (research/mp_advanced_20261024), and the current
// 30-minute period against what is normal for that time of day. Refreshed every minute.
function ProfileNotes({ symbol }: { symbol: string }) {
  const [data, setData] = useState<Annotations>();
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    const load = () => void fetch(`${API_URL}/api/mp/annotations/${encodeURIComponent(symbol)}`, { headers: headers() })
      .then((r) => (r.ok ? r.json() : undefined)).then((v) => { if (alive && v) setData(v as Annotations); }).catch(() => undefined);
    load();
    const timer = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(timer); };
  }, [symbol]);
  if (!data || data.error) return null;
  const p = data.period || {};
  const flag = (bad?: boolean) => (bad ? "negative" : "");
  return <section className="panel auction-notes">
    <div className="panel-title"><span>Period vs normal, and profile odds</span><span className="auction-flow-controls">{data.at}</span></div>
    {p.period && <div className="auction-stat-row">
      <div><span>Period</span><b>{p.period} · {p.starts}</b></div>
      <div><span>Volume vs usual</span><b className={flag(p.volume_abnormal)}>{p.rvol != null ? `${p.rvol.toFixed(1)}x` : "—"}</b>
        {p.rvol_p90 && <small>p90 {p.rvol_p90.toFixed(1)}x</small>}</div>
      <div><span>Range (ATR)</span><b className={flag(p.range_abnormal)}>{p.range_atr != null ? p.range_atr.toFixed(2) : "—"}</b>
        {p.range_usual != null && <small>usual {p.range_usual.toFixed(2)} · p90 {p.range_p90?.toFixed(2)}</small>}</div>
      <div><span>Flow one-sided</span><b className={flag(p.delta_abnormal)}>{p.delta_share != null ? `${(p.delta_share * 100).toFixed(0)}%` : "—"}</b>
        {p.delta_p90 != null && <small>p90 {(p.delta_p90 * 100).toFixed(0)}%</small>}</div>
      <div><span>Day high / low made here</span><b>{p.day_high_share != null ? `${(p.day_high_share * 100).toFixed(0)}% / ${((p.day_low_share || 0) * 100).toFixed(0)}%` : "—"}</b></div>
    </div>}
    <ul className="auction-note-list">{(data.notes || []).slice(1).map((n, i) => <li key={i}>{n}</li>)}</ul>
    <small className="auction-note-foot">Odds from 5,250 index sessions 2021-26. Naked POCs are shown as levels only: they were hit no more often than any price at the same distance.</small>
  </section>;
}

const VIEW_STORE = "macd.auctionView";

export function AuctionPage({ marketOpen }: { marketOpen?: boolean }) {
  const [view, setView] = useState(() => (recall(VIEW_STORE) === "context" ? "context" : "flow"));
  const pickView = (value: "flow" | "context") => { setView(value); remember(VIEW_STORE, value); };
  const { data: auctionSymbols } = useEndpoint<string[]>("/api/auction/symbols", []);
  if (view === "flow") {
    return <section className="ledger-page auction-page auction-flow-page">
      <AuctionTabs view={view} onView={pickView} />
      <AuctionWorkstation symbols={auctionSymbols || []} marketOpen={marketOpen} />
    </section>;
  }
  return <AuctionContextPage tabs={<AuctionTabs view={view} onView={pickView} />} symbols={auctionSymbols} />;
}

function AuctionTabs({ view, onView }: { view: string; onView: (v: "flow" | "context") => void }) {
  return <div className="auction-tabs" role="tablist">
    <button role="tab" aria-selected={view === "flow"} className={view === "flow" ? "active" : ""} onClick={() => onView("flow")}>Order flow</button>
    <button role="tab" aria-selected={view === "context"} className={view === "context" ? "active" : ""} onClick={() => onView("context")}>Auction context</button>
  </div>;
}

function AuctionContextPage({ tabs, symbols }: { tabs: ReactNode; symbols: string[] | undefined }) {
  const [symbol, setSymbol] = useState("");
  const pickSymbol = (value: string) => { setSymbol(value); remember(SYMBOL_STORE, value); };
  // The remembered symbol only survives while it still has stored sessions —
  // every panel here keys off /api/auction/*/<symbol>, so a retired series
  // would reopen the desk on seven empty panels.
  useEffect(() => {
    if (!symbols?.length) return;
    setSymbol((old) => {
      if (old && symbols.includes(old)) return old;
      const stored = recall(SYMBOL_STORE);
      if (symbols.includes(stored)) return stored;
      return symbols.find((s) => s.includes("NIFTY50")) || symbols[0];
    });
  }, [symbols]);

  const context = useEndpoint<AuctionContext>(symbol ? `/api/auction/context/${encodeURIComponent(symbol)}` : null, [symbol]);
  const rates = useEndpoint<BaseRates>(symbol ? `/api/auction/base-rates/${encodeURIComponent(symbol)}` : null, [symbol]);
  // Windows land once a minute; fetched on symbol change alone, the panel would show the open all day.
  const [minute, setMinute] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setMinute((count) => count + 1), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const [flowDay, setFlowDay] = useState("");
  // A session still in the raw tier grows by the minute; a condensed one never
  // changes, so only the latest (or an explicitly chosen) day needs the tick.
  const flow = useEndpoint<AuctionFlow>(
    symbol ? `/api/auction/flow/${encodeURIComponent(symbol)}${flowDay ? `?day=${flowDay}` : ""}` : null,
    [symbol, flowDay, flowDay ? 0 : minute]);

  const regimeId = context.data?.regime.regime_id;
  const journal = useEndpoint<SetupJournal>(
    symbol ? `/api/auction/setups/${encodeURIComponent(symbol)}${regimeId ? `?regime_id=${regimeId}` : ""}` : null, [symbol, regimeId]);
  const whale = useEndpoint<WhaleView>("/api/auction/whale", [symbol, minute]);

  const reference = context.data;
  return <section className="ledger-page auction-page">
    {tabs}
    <div className="page-heading auction-heading">
      <div><h1>Auction Context</h1>
        <p>Daily · weekly · monthly value, against this regime's own base rates</p></div>
      <div className="auction-controls">
        <select className="rrg-select" value={symbol} onChange={(event) => pickSymbol(event.target.value)} aria-label="Auction symbol">
          {(symbols || []).map((row) => <option key={row} value={row}>{row}</option>)}
        </select>
        {reference && <span className="mode" title={`${reference.regime.summary} · from ${reference.regime.start}`}>
          {reference.regime.regime_id}
        </span>}
        {reference && <span className={reference.sessions_available >= 20 ? "feed-badge" : "mode alert"}>
          {reference.sessions_available} stored sessions
        </span>}
      </div>
    </div>

    {!symbols?.length && <section className="panel auction-empty">
      No stored session profiles yet. Run <code>research/base_rates.py --rebuild</code> or wait for
      the engine's nightly build.
    </section>}

    {reference && <div className="auction-grid">
      <ProfileNotes symbol={symbol} />
      <LocationPanel reference={reference} />
      <LevelsPanel reference={reference} />
      <TodayPanel rates={rates.data} />
      <FlowPanel flow={flow.data} loading={flow.loading} onDay={setFlowDay} day={flowDay} />
      <BaseRatePanel rates={rates.data} error={rates.error} />
      <SetupPanel journal={journal.data} />
      <WhalePanel view={whale.data} />
      <WhaleNamesPanel minute={minute} />
    </div>}
    {!reference && <WhaleNamesPanel minute={minute} />}
  </section>;
}

/** Where price sits against each timeframe, and whether they agree. */
function LocationPanel({ reference }: { reference: AuctionContext }) {
  const tone = (value: string) => value === "above_value" ? "positive" : value === "below_value" ? "negative" : "";
  const label = (value: string) => (value || "unknown").replace(/_/g, " ");
  const aligned = reference.alignment.startsWith("aligned");
  return <section className="panel auction-location">
    <div className="panel-title"><span>Location</span>
      <span>{reference.price ? `${price.format(reference.price)}` : "no mark"}</span></div>
    <div className="auction-tf-row">
      {TIMEFRAMES.map(({ key, label: name }) => {
        const where = reference.location[key] || "unknown";
        return <div key={key} className="auction-tf">
          <span>{name}</span>
          <b className={tone(where)}>{label(where)}</b>
        </div>;
      })}
    </div>
    <div className={`auction-alignment ${aligned ? "aligned" : "conflicted"}`}>
      <b>{label(reference.alignment)}</b>
      <p>{aligned
        ? "All three timeframes agree on location. The rarer, cleaner read."
        : "The timeframes disagree — a move that is initiative on one is responsive on another."}</p>
    </div>
    <div className="auction-migration">
      <span>Value migration</span><b>{label(reference.value_migration)}</b>
      <p>How the last session's value area sat against the one before it.</p>
    </div>
  </section>;
}

/** Every reference level, sorted by price so the ladder reads like a chart. */
function LevelsPanel({ reference }: { reference: AuctionContext }) {
  const rows = useMemo(() => {
    const named: { name: string; value: number; kind: string }[] = [];
    Object.entries(reference.levels).forEach(([name, value]) => {
      const [scope, what] = name.split("_");
      named.push({ name: `${scope === "pd" ? "prior day" : scope} ${what}`.toUpperCase(), value, kind: what });
    });
    reference.naked_pocs.forEach((value) => named.push({ name: "NAKED POC", value, kind: "naked" }));
    return named.sort((a, b) => b.value - a.value);
  }, [reference]);
  const mark = reference.price;
  return <section className="panel auction-levels">
    <div className="panel-title"><span>Reference levels</span><span>{rows.length}</span></div>
    <div className="auction-ladder">
      {rows.map((row, index) => {
        const above = mark !== null && row.value > mark;
        return <div key={`${row.name}-${index}`} className={`auction-level ${row.kind}`}>
          <i className={above ? "above" : "below"} />
          <span>{row.name}</span>
          <b>{price.format(row.value)}</b>
          <small>{mark ? `${above ? "+" : ""}${((row.value - mark) / mark * 100).toFixed(2)}%` : ""}</small>
        </div>;
      })}
      {!rows.length && <p className="muted">No stored references for this symbol yet.</p>}
    </div>
    <p className="auction-note">Naked POC levels are prior points of control that no later session has reached.</p>
  </section>;
}

const rate = (value?: Rate) => (value && value[1] ? `${(100 * value[0] / value[1]).toFixed(1)}%` : "—");
const sample = (value?: Rate) => (value ? `${value[0]}/${value[1]}` : "");

/** Is today ordinary? Only meaningful against this regime's own sessions. */
function TodayPanel({ rates }: { rates?: BaseRates }) {
  const today = rates?.today;
  if (!today) return <section className="panel auction-today">
    <div className="panel-title"><span>Where today sits</span></div>
    <p className="muted">No measured session yet.</p>
  </section>;
  const m = today.measurement as Record<string, number | string | null>;
  return <section className="panel auction-today">
    <div className="panel-title"><span>Where today sits</span>
      <span>{today.day} · {today.regime_sessions} sessions in regime</span></div>
    <div className="auction-stat-row">
      <div><span>IB</span><b>{m.ib_width ? price.format(Number(m.ib_width)) : "—"}</b></div>
      <div><span>Break</span><b>{String(m.break_side ?? "—")}</b></div>
      <div><span>First break</span><b>{String(m.first_break_bracket ?? "—")}</b></div>
      <div><span>Extension / IB</span><b>{m.extension_ratio !== null ? Number(m.extension_ratio).toFixed(2) : "—"}</b></div>
      <div><span>Range / IB</span><b>{m.range_ib_ratio !== null ? Number(m.range_ib_ratio).toFixed(2) : "—"}</b></div>
    </div>
    <table className="auction-compare">
      <thead><tr><th>Today</th><th>Base rate</th><th>n</th></tr></thead>
      <tbody>
        {today.comparisons.map((row) => <tr key={row.label}>
          <td><i className={row.today ? "yes" : "no"} />{row.label}</td>
          <td>{row.base_rate.toFixed(1)}%</td>
          <td className="muted">{row.sample}</td>
        </tr>)}
      </tbody>
    </table>
    <p className="auction-note">A number is only remarkable against its own regime. Comparing
      today to an average spanning a lot-size change is how an ordinary session looks unusual.</p>
  </section>;
}

/** The book against the tape. Two measurements, never blended. */
function FlowPanel({ flow, loading, day, onDay }: {
  flow?: AuctionFlow; loading: boolean; day: string; onDay: (value: string) => void;
}) {
  return <section className="panel auction-flow">
    <div className="panel-title"><span>Book pressure vs inferred delta</span>
      <span className="auction-flow-controls">
        {flow?.mean_confidence !== null && flow?.mean_confidence !== undefined &&
          <em className={flow.mean_confidence >= 0.6 ? "feed-badge" : "mode alert"}>
            confidence {flow.mean_confidence.toFixed(2)}</em>}
        <select className="rrg-select" value={day} onChange={(event) => onDay(event.target.value)} aria-label="Flow session">
          {(flow?.available_days || []).map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </span>
    </div>
    {flow && flow.minutes > 0 && !flow.series.some((row) => row.ofi_events) &&
      <div className="auction-divergence">
        No book events for this instrument. An index has no order book of its own, so OFI
        and inferred delta are both structurally zero — read this on a future or an equity.
      </div>}
    {flow?.diverged && <div className="auction-divergence">
      Cumulative OFI and CVD disagree in sign. Pressure is entering the book that never became
      a print — limit orders stacked or pulled rather than traded.
    </div>}
    <FlowChart series={flow?.series || []} loading={loading} />
    <div className="auction-stat-row">
      <div><span>Cumulative OFI</span><b className={(flow?.cumulative_ofi || 0) >= 0 ? "positive" : "negative"}>
        {flow ? compact.format(flow.cumulative_ofi) : "—"}</b></div>
      <div><span>Cumulative delta</span><b className={(flow?.cumulative_delta || 0) >= 0 ? "positive" : "negative"}>
        {flow ? compact.format(flow.cumulative_delta) : "—"}</b></div>
      <div><span>Minutes</span><b>{flow?.minutes ?? 0}</b></div>
    </div>
    <p className="auction-note">OFI reads the book, which the feed transmits exactly. Delta infers
      the aggressor, which this feed never states. They are not interchangeable.</p>
  </section>;
}

function FlowChart({ series, loading }: { series: AuctionFlow["series"]; loading: boolean }) {
  const element = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; ofi: ISeriesApi<"Line">; cvd: ISeriesApi<"Line"> }>();
  useEffect(() => {
    if (!element.current) return;
    const chart = createChart(element.current, {
      width: element.current.clientWidth, height: element.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#090e16" }, textColor: "#8796aa", attributionLogo: false },
      grid: { vertLines: { color: "#151e2b" }, horzLines: { color: "#151e2b" } },
      rightPriceScale: { borderColor: "#263144", minimumWidth: 64 },
      timeScale: { borderColor: "#263144", timeVisible: true, secondsVisible: false,
        tickMarkFormatter: (value: Time) => typeof value === "number" ? CLOCK.format(value * 1000) : "" },
      localization: { timeFormatter: (value: Time) => typeof value === "number" ? `${CLOCK.format(value * 1000)} IST` : "" },
      crosshair: { vertLine: { color: "#61728a", labelBackgroundColor: "#243247" }, horzLine: { color: "#61728a", labelBackgroundColor: "#243247" } },
    });
    const ofi = chart.addSeries(LineSeries, { color: "#55a9ff", lineWidth: 2, title: "cum OFI", priceLineVisible: false });
    const cvd = chart.addSeries(LineSeries, { color: "#ffad42", lineWidth: 2, lineStyle: LineStyle.Dashed, title: "CVD", priceLineVisible: false });
    ofi.createPriceLine({ price: 0, color: "#71819877", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    api.current = { chart, ofi, cvd };
    const observer = new ResizeObserver(() => chart.applyOptions({ width: element.current?.clientWidth, height: element.current?.clientHeight }));
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.remove(); api.current = undefined; };
  }, []);
  useEffect(() => {
    if (!api.current) return;
    // Both series are quantity, but OFI counts book size and CVD counts traded
    // size; they share a scale so the shapes can be compared, not the levels.
    api.current.ofi.setData(series.map((row) => ({ time: row.time as Time, value: row.cumulative_ofi })));
    api.current.cvd.setData(series.map((row) => ({ time: row.time as Time, value: row.cvd })));
    if (series.length) api.current.chart.timeScale().fitContent();
  }, [series]);
  return <div className="auction-chart" ref={element}>
    {!series.length && <div className="chart-empty">{loading ? "Loading session flow…" : "No recorded flow for this session yet."}</div>}
  </div>;
}

const ROWS: { label: string; key: keyof BaseRateGroup["summary"]; indent?: boolean }[] = [
  { label: "IB broken by the close", key: "ib_broken" },
  { label: "break up only", key: "break_up_only", indent: true },
  { label: "break down only", key: "break_down_only", indent: true },
  { label: "both sides (neutral)", key: "break_both", indent: true },
  { label: "first break in bracket C", key: "first_break_in_C" },
  { label: "extension ≥ 25% of IB", key: "extension_over_25pct" },
  { label: "extension ≥ 50% of IB", key: "extension_over_50pct" },
  { label: "extension ≥ 100% of IB", key: "extension_over_100pct" },
  { label: "opened outside prior value", key: "opened_outside_value" },
  { label: "returned to value", key: "returned_to_value", indent: true },
  { label: "80% rule triggered", key: "rule80_triggered", indent: true },
  { label: "80% rule completed", key: "rule80_completed", indent: true },
  { label: "gapped beyond prior range", key: "gapped" },
  { label: "gap filled same day", key: "gap_filled", indent: true },
];

function BaseRatePanel({ rates, error }: { rates?: BaseRates; error: string }) {
  const [group, setGroup] = useState(() => recall(REGIME_STORE));
  const groups = rates?.groups || [];
  // Newest regime first: the base rate that matters is the one for the rules
  // in force now, not the oldest table that happens to sort first. The same
  // lookup retires a remembered regime the current symbol no longer publishes.
  const current = [...groups].reverse().find((row) => row.key !== "all");
  const active = groups.find((row) => row.key === group) || current || groups[0];
  return <section className="panel auction-rates">
    <div className="panel-title"><span>Base rates</span>
      <span>{rates?.span ? `${rates.span[0]} → ${rates.span[1]}` : ""}</span></div>
    <div className="auction-regime-tabs">
      {groups.map((row) => <button key={row.key} className={active?.key === row.key ? "active" : ""}
        onClick={() => { setGroup(row.key); remember(REGIME_STORE, row.key); }} title={row.title}>
        {row.key === "all" ? "All" : row.key}<span>{row.sessions}</span>
      </button>)}
    </div>
    {active?.key === "all" && !!rates?.crosses.length && <div className="auction-divergence">
      This pooled table crosses {rates.crosses.length} rule change{rates.crosses.length > 1 ? "s" : ""}
      {" "}({rates.crosses.join(", ")}) and describes no single market. Pick a regime.
    </div>}
    {active && <table className="auction-rate-table">
      <tbody>
        {ROWS.map((row) => <tr key={row.key} className={row.indent ? "indent" : ""}>
          <td>{row.label}</td>
          <td><b>{rate(active.summary[row.key] as Rate)}</b></td>
          <td className="muted">{sample(active.summary[row.key] as Rate)}</td>
        </tr>)}
        <tr><td>median extension / IB</td>
          <td><b>{active.summary.median_extension_ratio?.toFixed(2) ?? "—"}</b></td><td /></tr>
        <tr><td>median range / IB</td>
          <td><b>{active.summary.median_range_ib_ratio?.toFixed(2) ?? "—"}</b></td><td /></tr>
      </tbody>
    </table>}
    {error && <p className="negative">{error}</p>}
    <p className="auction-note">Every rate carries its denominator. The interesting ones here are
      rare, and a percentage without its sample size invites reading ten triggers as a finding.</p>
  </section>;
}

export function sessionSummary(row: ProfileRow | null): string {
  if (!row) return "—";
  return `${row.day} · POC ${price.format(row.poc || 0)} · ${row.day_type || "unknown"}`;
}


/** Part 7 as a journal: for each setup, how often its context appeared, its
 *  trigger fired, and the promised outcome followed -- by regime. */
function SetupPanel({ journal }: { journal?: SetupJournal }) {
  const rows = journal?.summary || [];
  return <section className="panel auction-setups">
    <div className="panel-title"><span>Setup journal</span>
      <span>{journal?.regime_id ? `regime ${journal.regime_id}` : "all regimes"}</span></div>
    <table className="auction-rate-table auction-setup-table">
      <thead><tr><th>Setup</th><th>Context</th><th>Trigger</th><th>Outcome</th><th /></tr></thead>
      <tbody>
        {rows.map((row) => <tr key={row.setup} className={row.measurable ? "" : "unmeasurable"}>
          <td><b>{row.setup}</b> {row.name}<small>{row.kind}{row.claimed_edge ? ` · claimed: ${row.claimed_edge}` : ""}</small></td>
          <td>{row.measurable ? rate(row.context) : "—"}<small>{sample(row.context)}</small></td>
          <td>{row.measurable ? rate(row.triggered) : "—"}<small>{sample(row.triggered)}</small></td>
          <td>{row.measurable ? rate(row.outcome) : "—"}<small>{sample(row.outcome)}</small></td>
          <td className="muted">{row.measurable ? "" : "needs tick features"}</td>
        </tr>)}
        {!rows.length && <tr><td colSpan={5} className="muted">No journal rows yet — the nightly job writes them after each close.</td></tr>}
      </tbody>
    </table>
    <p className="auction-note">Outcome is conditional on the trigger, trigger on the context. A setup earns
      a position only once its outcome rate, net of costs, beats the base rate for its regime.</p>
  </section>;
}

const sideLabel = (side: number) => side > 0 ? "buy" : side < 0 ? "sell" : "?";
const crore = (rupees: number | null | undefined) => `₹${((rupees ?? 0) / 1e7).toFixed(1)} cr`;
const signed = (value: number) => `${value >= 0 ? "+" : ""}${value}`;
const HORIZONS = [15, 30, 60];

/** Whale tracker. Layer A: shapes the freeze quantity forces a large participant into. Layer B: the
 *  per-strike ΔOI window, delta-weighted. Layer C: the futures leg beside the option book. Layer D:
 *  yesterday's close. Layer E: the composite, which says "insufficient history" until twenty sessions exist. */
function WhalePanel({ view }: { view?: WhaleView }) {
  const scores = Object.entries(view?.aggression || {}).sort((a, b) => Math.abs(b[1].net) - Math.abs(a[1].net));
  const chains = Object.entries(view?.chains || {}).filter(([, c]) => c.snapshots > 0);
  const windows = Object.entries(view?.windows || {});
  const closes = Object.entries(view?.eod || {});
  const thin = view?.history && view.history.chain_days < view.history.required;
  return <section className="panel auction-whale">
    <div className="panel-title"><span>Whale tracker · layers A–E</span><span>{view?.day || ""}</span></div>
    {scores.length ? <div className="auction-stat-row">
      {scores.slice(0, 4).map(([sym, score]) => <div key={sym}>
        <span>{sym.replace(/^NSE:|^BSE:/, "")}</span>
        <b className={score.net >= 0 ? "positive" : "negative"}>{score.net >= 0 ? "+" : ""}{score.net.toFixed(2)}</b>
      </div>)}
    </div> : <p className="muted">No tick-level events recorded for this session.</p>}
    <div className="auction-whale-events">
      {(view?.events || []).slice(0, 12).map((row) => <div key={`${row.symbol}-${row.ts_ms}-${row.kind}`} className={`auction-whale-event ${row.side > 0 ? "buy" : row.side < 0 ? "sell" : ""}`}>
        <span>{CLOCK.format(row.ts_ms)}</span>
        <b>{row.kind.replace(/_/g, " ")}</b>
        <em>{sideLabel(row.side)} {row.quantity ?? ""}</em>
        <small>{row.symbol.replace(/^NSE:|^BSE:/, "")} · {row.evidence}</small>
      </div>)}
    </div>
    {windows.map(([root, w]) => <div key={root} className="auction-whale-window">
      <div className="auction-chain-head"><b>{root} · {CLOCK.format(w.as_of * 1000)} window</b>
        <span>{w.composite_decayed == null ? `insufficient history ${w.history.days}/${w.history.required}` : `composite ${w.composite_decayed.toFixed(2)}`}</span></div>
      <div className="auction-stat-row">
        <div><span>option Δ-notional</span><b className={w.net_option_delta.sign >= 0 ? "positive" : "negative"}>{crore(w.net_option_delta.dn)}</b></div>
        <div><span>futures CVD</span><b className={(w.futures?.delta_units ?? 0) >= 0 ? "positive" : "negative"}>{w.futures ? `${compact.format(w.futures.delta_units / w.lot)} L` : "—"}</b></div>
        <div><span>fut ΔOI</span><b>{w.futures?.d_oi != null ? `${compact.format(w.futures.d_oi / w.lot)} L` : "no OI"}</b></div>
        <div><span>PCR (OI)</span><b className={w.pcr_jumped ? "negative" : ""}>{w.pcr_oi ?? "—"}{w.pcr_jump != null ? ` (${signed(w.pcr_jump)})` : ""}</b></div>
        <div><span>signed legs</span><b>{w.net_option_delta.signed_legs}/{w.net_option_delta.signed_legs + w.net_option_delta.unsigned_legs}</b></div>
      </div>
      {w.divergence.divergence && <div className="auction-whale-divergence">
        Futures {(w.divergence.fut_sign ?? 0) > 0 ? "buying" : "selling"} against a {w.net_option_delta.sign > 0 ? "long" : "short"} option book ×{w.divergence.ratio}{w.divergence.fresh_futures ? " · fresh futures OI" : ""}
      </div>}
      <div className="auction-chain-strikes">
        {w.strikes.slice(0, 8).map((s) => <span key={`${s.strike}${s.option_type}`} className={s.contribution >= 0 ? "positive" : "negative"}
          title={`Δ ${s.delta.toFixed(2)} (${s.delta_source}) · flow ${s.flow_source}${s.unusual.length ? " · " + s.unusual.join(", ") : ""}`}>
          {s.strike} {s.option_type} {signed(Math.trunc(s.d_oi / w.lot))}L {s.flow_sign > 0 ? "▲" : s.flow_sign < 0 ? "▼" : "·"} {crore(s.dn)}{s.unusual.length ? " !" : ""}</span>)}
      </div>
      {w.structures.length > 0 && <div className="auction-whale-structures">
        {w.structures.map((st, i) => <em key={i} className={st.direction > 0 ? "buy" : st.direction < 0 ? "sell" : ""}>{st.kind.replace(/_/g, " ")} {crore(st.dn)}</em>)}
      </div>}
    </div>)}
    {(view?.alerts || []).map((a) => <div key={a.id} className="auction-whale-alert">
      <span>{CLOCK.format(a.ts * 1000)}</span>
      <b>{a.underlying} {a.composite.toFixed(1)} {a.direction > 0 ? "long" : a.direction < 0 ? "short" : ""}</b>
      <em>{[a.next_15, a.next_30, a.next_60].map((o, i) => o ? `${HORIZONS[i]}m ${signed(o.move_pts)}${o.agreed ? "✓" : "✗"}` : `${HORIZONS[i]}m …`).join(" · ")}</em>
    </div>)}
    {closes.map(([root, e]) => <p key={root} className="auction-note">
      {root} close {e.day}: PCR {e.pcr_oi ?? "—"}{e.prior_pcr_oi != null ? ` (was ${e.prior_pcr_oi})` : ""} · ΔOI calls {compact.format(e.d_call_oi)} puts {compact.format(e.d_put_oi)}
      {e.fut_oi != null ? ` · fut OI ${compact.format(e.fut_oi)}` : " · fut OI —"}{e.fut_avg_trade_pct != null ? ` · avg print p${e.fut_avg_trade_pct}` : ""}
    </p>)}
    {chains.map(([root, chain]) => <div key={root} className="auction-chain">
      <div className="auction-chain-head"><b>{root} latest chain</b>
        <span>PCR (OI) {chain.pcr_oi ?? "—"} · PCR (vol) {chain.pcr_volume ?? "—"} · {chain.snapshots < 2 ? "one snapshot, no window yet" : "3-minute ΔOI, units"}</span></div>
      <div className="auction-chain-strikes">
        {chain.strikes.slice(0, 6).map((row) => <span key={`${row.strike}${row.type}`} className={row.d_oi >= 0 ? "positive" : "negative"}>
          {row.strike} {row.type} {row.d_oi >= 0 ? "+" : ""}{compact.format(row.d_oi)}</span>)}
      </div>
    </div>)}
    {thin && <p className="auction-note auction-whale-insufficient">Chain history {view!.history.chain_days}/{view!.history.required} sessions:
      absolute notices (volume ≥ OI, ≥5× divergence, structures) are live; z-scores, the composite and alerts start at {view!.history.required}.</p>}
    <p className="auction-note">Shapes, not names. A slicer is three freeze-size prints on one side inside a
      minute; a strike's flow arrow is classified prints where the desk streams the leg and a premium proxy elsewhere.</p>
  </section>;
}


type WhaleName = {
  symbol: string; kind: "cash" | "future" | "option" | "index"; underlying: string;
  strike: number | null; right: "CE" | "PE" | null; lot: number; score: number; events: number;
  counts: Record<string, number>; patterns: string[];
  top: { kind: string; side: number; quantity: number; price: number; score: number; evidence: string; at: string };
  largest: { quantity: number; lots: number; price: number; notional: number; median_multiple: number | null; side: number; at: string };
  child: { size: number; lots: number; runs: number; every_seconds: number | null; cv: number | null; side: number; notional: number; last_at: string } | null;
  side: "buy" | "sell" | "mixed"; buy_quantity: number; sell_quantity: number;
  session_volume: number | null; large_share_pct: number | null; first_at: string; last_at: string;
  entry?: EntryPerf & { at: string; price: number; side: number };
  probability?: number | null; calibration_run?: number | null; features?: Record<string, string>;
  size_vs_p999?: number | null; turnover_share_pct?: number | null;
};
type EntryPerf = {
  bars?: number; sessions?: number; max_return_pct?: number; max_at?: string; min_return_pct?: number; min_at?: string;
  last_return_pct?: number | null; last_at?: string; favourable_pct?: number; adverse_pct?: number;
  forward?: Record<string, number | null>; complete?: boolean;
};
type PerfSummary = { entries?: number; measured?: number; avg_max_return_pct?: number | null; avg_min_return_pct?: number | null;
  avg_favourable_pct?: number | null; avg_adverse_pct?: number | null; went_their_way_pct?: number | null };
type WhalePerformance = {
  since: string; until: string; follow_sessions: number; summary: PerfSummary;
  by_side: Record<string, PerfSummary>; by_pattern: Record<string, PerfSummary>;
  entries: (EntryPerf & { symbol: string; kind: string; underlying: string; strike: number | null; right: string | null;
    day: string; entry_at: string; entry_price: number; side: number; pattern: string; score: number; evidence: string })[];
};
const pct = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
const tone = (value: number | null | undefined) => value == null ? "" : value > 0 ? "positive" : value < 0 ? "negative" : "";
type WhaleNames = {
  day: string; count: number; names: WhaleName[];
  thresholds: { large_print_multiple: number; median_window_minutes: number; min_large_print_lots: number;
    child_min_repeats: number; child_max_cv: number; slicer_window_seconds: number; slicer_min_prints: number;
    feed_gap_seconds: number; scan_every_minutes: number;
    notional_floor: Record<string, { large_print: number; child: number }> };
  feed_gaps: { from: string; to: string; seconds: number }[];
};

const WHALE_KINDS = [["", "All"], ["cash", "Stocks"], ["future", "Futures"], ["option", "Options"]] as const;
const QTY = new Intl.NumberFormat("en-IN");
const rupees = (value: number | null | undefined) => {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= 1e7) return `₹${(value / 1e7).toFixed(2)} Cr`;
  if (value >= 1e5) return `₹${(value / 1e5).toFixed(1)} L`;
  return `₹${QTY.format(Math.round(value))}`;
};
const instrumentLabel = (row: WhaleName) => row.kind === "option" && row.strike != null
  ? `${row.underlying} ${QTY.format(row.strike)} ${row.right}` : row.symbol.replace(/^NSE:|^BSE:/, "");

/** Every contract with a Layer A pattern today, ranked, with the numbers behind each pattern. */
function WhaleNamesPanel({ minute }: { minute: number }) {
  const [kind, setKind] = useState("");
  const [mode, setMode] = useState<"NAMES" | "PERFORMANCE" | "TUNING">("NAMES");
  const names = useEndpoint<WhaleNames>(mode === "NAMES" ? `/api/auction/whale/names?limit=150${kind ? `&kind=${kind}` : ""}` : null, [kind, minute, mode]);
  const perf = useEndpoint<WhalePerformance>(mode === "PERFORMANCE" ? `/api/auction/whale/performance?days=10${kind ? `&kind=${kind}` : ""}` : null, [kind, minute, mode]);
  const data = names.data;
  const t = data?.thresholds;
  return <section className="panel auction-whale-names">
    <div className="panel-title"><span>Whale names · large prints, icebergs, freeze-size slicing</span>
      <span>{data ? `${data.count} names · ${data.day}` : names.loading ? "Loading…" : ""}</span></div>
    <div className="auction-whale-names-bar">
      <div className="radar-filter" role="tablist" aria-label="View">
        <button className={mode === "NAMES" ? "active" : ""} aria-pressed={mode === "NAMES"} onClick={() => setMode("NAMES")}>Today's names</button>
        <button className={mode === "PERFORMANCE" ? "active" : ""} aria-pressed={mode === "PERFORMANCE"} onClick={() => setMode("PERFORMANCE")}
          title="Every entry to the list over the last 10 days, and what the instrument did after it">Performance after entry</button>
        <button className={mode === "TUNING" ? "active" : ""} aria-pressed={mode === "TUNING"} onClick={() => setMode("TUNING")}
          title="Per-instrument thresholds, feature weights and the nightly calibration runs">Tuning</button>
      </div>
      <div className="radar-filter" role="tablist" aria-label="Instrument kind">
        {WHALE_KINDS.map(([key, label]) => <button key={key || "all"} className={kind === key ? "active" : ""}
          aria-pressed={kind === key} onClick={() => setKind(key)}>{label}</button>)}
      </div>
    </div>
    {mode === "TUNING" ? <WhaleTuningView /> : mode === "PERFORMANCE" ? <WhalePerformanceView view={perf.data} loading={perf.loading} error={perf.error} /> : <>
    {names.error && <p className="auction-note">{names.error}</p>}
    <div className="auction-whale-names-scroll">
      <table className="auction-rate-table auction-whale-names-table">
        <thead><tr>
          <th>#</th><th>Instrument</th><th>Patterns</th><th>Lean</th>
          <th>Largest print</th><th>× median</th><th>Notional</th><th>Iceberg clip</th>
          <th>Freeze / slicer</th><th>Whale % of vol</th><th>Events</th><th>Score</th><th>Seen</th>
          <th title="First appearance on today's list: time and price of its first pattern">Entry</th>
          <th title="Best and worst move from the entry price since entry (minute bars)">Max since entry</th>
          <th>Min since entry</th><th>Now</th>
          <th title="Calibrated probability the move goes the whale's way (≥ half the instrument's typical 30-minute move), from the latest tuning run">P(their way)</th>
        </tr></thead>
        <tbody>
          {(data?.names || []).map((row, index) => <tr key={row.symbol} title={row.top.evidence}>
            <td>{index + 1}</td>
            <td><b>{instrumentLabel(row)}</b><small> {row.kind === "cash" ? "stock" : row.kind}{row.lot > 1 ? ` · lot ${row.lot}` : ""}</small></td>
            <td>{row.patterns.join(" · ") || row.top.kind.replace(/_/g, " ")}</td>
            <td className={row.side === "buy" ? "positive" : row.side === "sell" ? "negative" : ""}>{row.side}</td>
            <td>{QTY.format(row.largest.quantity)}{row.lot > 1 ? ` (${QTY.format(row.largest.lots)} lots)` : ""} @ {row.largest.price} · {row.largest.at} {sideLabel(row.largest.side)}</td>
            <td>{row.largest.median_multiple != null ? `${row.largest.median_multiple}×` : "—"}</td>
            <td>{rupees(row.largest.notional)}</td>
            <td>{row.child ? `${QTY.format(row.child.size)} × ${row.child.runs} run${row.child.runs > 1 ? "s" : ""}${row.child.every_seconds ? ` · every ${row.child.every_seconds}s` : ""}${row.child.cv != null ? ` · cv ${row.child.cv}` : ""} · ${rupees(row.child.notional)}` : "—"}</td>
            <td>{(row.counts.freeze_print || 0) + (row.counts.slicer || 0) ? `${row.counts.freeze_print || 0} / ${row.counts.slicer || 0}` : "—"}</td>
            <td>{row.large_share_pct != null ? `${row.large_share_pct}%` : "—"}</td>
            <td>{row.events}</td>
            <td>{row.score.toFixed(2)}</td>
            <td>{row.first_at === row.last_at ? row.first_at : `${row.first_at}–${row.last_at}`}</td>
            <td>{row.entry ? `${row.entry.at} @ ${row.entry.price}` : "—"}</td>
            <td className={tone(row.entry?.max_return_pct)}>{pct(row.entry?.max_return_pct)}{row.entry?.max_at ? <small> {row.entry.max_at.slice(-5)}</small> : null}</td>
            <td className={tone(row.entry?.min_return_pct)}>{pct(row.entry?.min_return_pct)}{row.entry?.min_at ? <small> {row.entry.min_at.slice(-5)}</small> : null}</td>
            <td className={tone(row.entry?.last_return_pct)}>{pct(row.entry?.last_return_pct)}</td>
            <td title={row.features ? Object.entries(row.features).map(([k, v]) => `${k}: ${v}`).join(" · ")
              + (row.size_vs_p999 != null ? ` · ${row.size_vs_p999}× own p99.9` : "") : "not calibrated yet"}>
              {row.probability != null ? `${(row.probability * 100).toFixed(0)}%` : "—"}</td>
          </tr>)}
          {data && !data.names.length && <tr><td colSpan={18} className="empty">No whale patterns recorded for this filter yet.</td></tr>}
        </tbody>
      </table>
    </div>
    {t && <p className="auction-note">
      Large print ≥ {t.large_print_multiple}× the {t.median_window_minutes}-minute median size and ≥ {t.min_large_print_lots} lots;
      iceberg = {t.child_min_repeats}+ identical clips at regular spacing (cv &lt; {t.child_max_cv}); slicer = {t.slicer_min_prints}+ freeze-size
      prints in {t.slicer_window_seconds}s (NIFTY/BANKNIFTY). Minimum notional — stocks {rupees(t.notional_floor.cash?.large_print)} / clip {rupees(t.notional_floor.cash?.child)},
      options {rupees(t.notional_floor.option?.large_print)} / {rupees(t.notional_floor.option?.child)} premium. Continuous session only; the updates
      straight after a feed gap of {t.feed_gap_seconds}s+ are excluded{data?.feed_gaps.length ? ` (today: ${data.feed_gaps.map((g) => `${g.from}–${g.to}`).join(", ")})` : ""}.
      Re-scanned every {t.scan_every_minutes} min during the session; the nightly pass redoes the whole day. Sizes are feed clusters, not single orders.
    </p>}
    </>}
  </section>;
}

/** Entries to the list over the last days, and what each instrument did after it entered. */
function WhalePerformanceView({ view, loading, error }: { view?: WhalePerformance; loading: boolean; error: string }) {
  if (error) return <p className="auction-note">{error}</p>;
  if (!view) return <p className="muted">{loading ? "Loading…" : ""}</p>;
  const cards: [string, PerfSummary | undefined][] = [["All entries", view.summary], ["Buy side", view.by_side.buy], ["Sell side", view.by_side.sell],
    ...Object.entries(view.by_pattern).map(([k, v]) => [k.replace(/_/g, " "), v] as [string, PerfSummary])];
  return <>
    <div className="auction-whale-perf-cards">
      {cards.filter(([, s]) => s && s.entries).map(([label, s]) => <div key={label}>
        <span>{label} · {s!.measured}/{s!.entries}</span>
        <b className={tone(s!.avg_favourable_pct)}>{s!.went_their_way_pct != null ? `${s!.went_their_way_pct}% their way` : "—"}</b>
        <small>avg max {pct(s!.avg_max_return_pct)} · avg min {pct(s!.avg_min_return_pct)}{s!.avg_favourable_pct != null ? ` · with ${pct(s!.avg_favourable_pct)} / against ${pct(s!.avg_adverse_pct)}` : ""}</small>
      </div>)}
    </div>
    <div className="auction-whale-names-scroll">
      <table className="auction-rate-table auction-whale-names-table">
        <thead><tr><th>Day</th><th>Instrument</th><th>Pattern</th><th>Side</th><th>Entry</th>
          <th>+15m</th><th>+30m</th><th>+60m</th><th>Max</th><th>Min</th><th>With side</th><th>Against</th><th>Now</th><th>Followed</th></tr></thead>
        <tbody>
          {view.entries.map((row) => <tr key={`${row.symbol}-${row.day}`} title={row.evidence}>
            <td>{row.day}</td>
            <td><b>{row.kind === "option" && row.strike != null ? `${row.underlying} ${row.strike} ${row.right}` : row.symbol.replace(/^NSE:|^BSE:/, "")}</b></td>
            <td>{row.pattern.replace(/_/g, " ")}</td>
            <td className={row.side > 0 ? "positive" : row.side < 0 ? "negative" : ""}>{sideLabel(row.side)}</td>
            <td>{row.entry_at} @ {row.entry_price}</td>
            {["15m", "30m", "60m"].map((k) => <td key={k} className={tone(row.forward?.[k])}>{pct(row.forward?.[k])}</td>)}
            <td className={tone(row.max_return_pct)}>{pct(row.max_return_pct)}<small> {row.max_at ?? ""}</small></td>
            <td className={tone(row.min_return_pct)}>{pct(row.min_return_pct)}<small> {row.min_at ?? ""}</small></td>
            <td className={tone(row.favourable_pct)}>{pct(row.favourable_pct)}</td>
            <td className={tone(row.adverse_pct)}>{pct(row.adverse_pct)}</td>
            <td className={tone(row.last_return_pct)}>{pct(row.last_return_pct)}</td>
            <td>{row.sessions ?? 0}/{view.follow_sessions + 1} sessions{row.complete ? " · done" : ""}</td>
          </tr>)}
          {!view.entries.length && <tr><td colSpan={14} className="empty">No entries recorded in this window yet.</td></tr>}
        </tbody>
      </table>
    </div>
    <p className="auction-note">An entry is a name's first appearance on a day's list, at the price of its first pattern. Returns come from
      1-minute bars over the rest of that session and {view.follow_sessions} more. "With side" is the best move in the whale's direction
      (up for a buy, down for a sell), "against" the worst; "their way" counts entries whose move with the side exceeded the move against it.</p>
  </>;
}


type TuningBin = { n: number; hits: number; hit_rate: number; weight: number; avg_ret30_pct: number | null };
type TuningOverview = {
  latest: ({ run_id: number; as_of: string; labelled: number; base_rate: number | null; top_quintile_hit_rate?: number;
    mean_brier?: number; features: Record<string, Record<string, TuningBin>> }) | null;
  runs: { run_id: number; created_at: string; as_of: string; labelled: number; base_rate: number | null;
    top_quintile_hit_rate?: number | null; mean_brier?: number | null }[];
  params: { symbols: number; overrides: number; avg_sensitivity: number | null; tightened: number; loosened: number };
  pending_labels: number; constants: Record<string, unknown>;
};
type TuningParam = { symbol: string; kind: string; sensitivity: number; source: string | null; large_min_qty: number | null;
  large_floor: number | null; child_min_qty: number | null; child_floor: number | null; window_share: number | null;
  legacy: boolean | null; disabled?: boolean | null; baseline_sessions: number | null; p50: number | null; p99: number | null;
  p999: number | null; turnover: number | null; typical_move_pct: number | null; override: Record<string, unknown> | null };
type TuningSymbol = { symbol: string; params: Record<string, unknown>; sensitivity: number; override: Record<string, unknown> | null;
  baseline: Record<string, number | null> | null; fitted_for: string | null;
  fingerprints: { day: string; prints: number; volume: number; notional: number; p50: number; p90: number; p99: number; p999: number; max_qty: number }[];
  events: { day: string; at: string; kind: string; side: number; bins: Record<string, string>; features: Record<string, unknown>;
    probability: number | null; ret30: number | null; typical_move: number | null; hit: number | null }[] };

/** Per-instrument thresholds, the feature weights outcomes have taught, and the runs that taught them. */
function WhaleTuningView() {
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState("");
  const [running, setRunning] = useState("");
  const overview = useEndpoint<TuningOverview>("/api/auction/whale/tuning", [revision]);
  const params = useEndpoint<TuningParam[]>(`/api/auction/whale/tuning/params?limit=400${query ? `&q=${encodeURIComponent(query)}` : ""}`, [query, revision]);
  const detail = useEndpoint<TuningSymbol>(picked ? `/api/auction/whale/tuning/symbol/${encodeURIComponent(picked)}` : null, [picked, revision]);
  const latest = overview.data?.latest;
  const runNow = () => {
    setRunning("Running…");
    void fetch(`${API_URL}/api/auction/whale/tuning/run`, { method: "POST", headers: headers() })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "run failed");
        setRunning(`Run ${body.calibration_run}: ${body.events} events on ${body.names} names · ${body.labelled} labelled · ${body.refitted} refitted`);
        setRevision((n) => n + 1);
      })
      .catch((error) => setRunning(error instanceof Error ? error.message : "run failed"));
  };
  return <div className="whale-tuning">
    <WhaleStudyBlock />
    <div className="auction-whale-perf-cards">
      <div><span>Latest calibration</span><b>{latest ? `run ${latest.run_id} · ${latest.as_of}` : "none yet"}</b>
        <small>{latest ? `${latest.labelled} labelled · base ${latest.base_rate != null ? (latest.base_rate * 100).toFixed(1) + "%" : "—"} · top-20% ${latest.top_quintile_hit_rate != null ? (latest.top_quintile_hit_rate * 100).toFixed(1) + "%" : "—"} · Brier ${latest.mean_brier ?? "—"}` : "runs nightly after the close"}</small></div>
      <div><span>Tuned instruments</span><b>{overview.data?.params.symbols ?? 0}</b>
        <small>{overview.data ? `${overview.data.params.overrides} overrides · sensitivity avg ${overview.data.params.avg_sensitivity ?? "—"} · ${overview.data.params.tightened} tightened / ${overview.data.params.loosened} loosened` : ""}</small></div>
      <div><span>Awaiting outcome</span><b>{overview.data?.pending_labels ?? 0}</b><small>events whose next hour has not traded yet</small></div>
      <div><span>Run the loop</span><button className="of-toggle" onClick={runNow} disabled={running === "Running…"}>Run tuning now</button>
        <small>{running || "rescan · fingerprint · label · calibrate · refit (after the close)"}</small></div>
    </div>
    {latest && Object.keys(latest.features || {}).length > 0 && <div className="whale-tuning-weights">
      {Object.entries(latest.features).map(([feature, bins]) => <table key={feature} className="auction-rate-table">
        <thead><tr><th>{feature.replace(/_/g, " ")}</th><th>n</th><th>hit</th><th title="log-odds vs base rate, shrunk toward 0 for small n">weight</th><th>avg 30m</th></tr></thead>
        <tbody>{Object.entries(bins).sort((a, b) => b[1].weight - a[1].weight).map(([value, bin]) => <tr key={value}>
          <td>{value.replace(/_/g, " ")}</td><td>{bin.n}</td><td>{(bin.hit_rate * 100).toFixed(0)}%</td>
          <td className={bin.weight > 0.05 ? "positive" : bin.weight < -0.05 ? "negative" : ""}>{bin.weight > 0 ? "+" : ""}{bin.weight.toFixed(2)}</td>
          <td className={tone(bin.avg_ret30_pct)}>{pct(bin.avg_ret30_pct)}</td></tr>)}</tbody>
      </table>)}
    </div>}
    <div className="whale-tuning-split">
      <div>
        <input className="whale-tuning-search" type="search" placeholder="Filter symbols…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="auction-whale-names-scroll">
          <table className="auction-rate-table auction-whale-names-table">
            <thead><tr><th>Instrument</th><th>Source</th><th>Sens.</th><th>Sessions</th><th>p50 / p99.9 clip</th><th>Turnover</th>
              <th>Large ≥ qty</th><th>Large ≥ ₹</th><th>Clip ≥ qty</th><th>Clip ≥ ₹</th><th>30m move</th></tr></thead>
            <tbody>{(params.data || []).map((row) => <tr key={row.symbol} className={picked === row.symbol ? "openable-row active" : "openable-row"}
              onClick={() => setPicked(row.symbol)}>
              <td><b>{row.symbol.replace(/^NSE:|^BSE:/, "")}</b><small> {row.kind}{row.legacy ? " · legacy" : ""}{row.disabled ? " · off" : ""}</small></td>
              <td>{row.source}{row.override ? " ✎" : ""}</td><td>{row.sensitivity}</td><td>{row.baseline_sessions ?? 0}</td>
              <td>{row.p50 != null ? `${QTY.format(row.p50)} / ${QTY.format(row.p999 ?? 0)}` : "—"}</td><td>{rupees(row.turnover)}</td>
              <td>{row.large_min_qty ? QTY.format(row.large_min_qty) : "—"}</td><td>{rupees(row.large_floor)}</td>
              <td>{row.child_min_qty ? QTY.format(row.child_min_qty) : "—"}</td><td>{rupees(row.child_floor)}</td>
              <td>{row.typical_move_pct != null ? `${row.typical_move_pct}%` : "—"}</td></tr>)}
              {params.data && !params.data.length && <tr><td colSpan={11} className="empty">No instrument has been tuned yet — the first run fits them.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      {picked && <TuningSymbolPanel view={detail.data} symbol={picked} onSaved={() => setRevision((n) => n + 1)} />}
    </div>
  </div>;
}

function TuningSymbolPanel({ view, symbol, onSaved }: { view?: TuningSymbol; symbol: string; onSaved: () => void }) {
  const [form, setForm] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  useEffect(() => {
    const o = view?.override || {};
    setForm(Object.fromEntries(["sensitivity", "large_min_qty", "large_floor", "window_share", "child_min_qty", "child_floor"]
      .map((k) => [k, o[k] != null ? String(o[k]) : ""])));
    setStatus("");
  }, [view]);
  const save = (clear = false) => {
    const body = clear ? null : Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, Number(v)]));
    void fetch(`${API_URL}/api/auction/whale/tuning/symbol/${encodeURIComponent(symbol)}`, {
      method: "PUT", headers: { ...(headers() || {}), "content-type": "application/json" }, body: JSON.stringify(body),
    }).then(async (response) => {
      if (!response.ok) throw new Error((await response.json()).detail || "save failed");
      setStatus(clear ? "Override cleared" : "Saved — thresholds refitted"); onSaved();
    }).catch((error) => setStatus(error instanceof Error ? error.message : "save failed"));
  };
  const labelled = (view?.events || []).filter((e) => e.hit != null);
  return <aside className="whale-tuning-detail">
    <div className="panel-title"><span>{symbol.replace(/^NSE:|^BSE:/, "")}</span><span>{view?.fitted_for ? `fitted for ${view.fitted_for}` : ""}</span></div>
    {view?.params && <p className="auction-note">
      Large print ≥ {String(view.params.large_multiple)}× 30m median, ≥ {QTY.format(Number(view.params.large_min_qty || 0))} qty,
      ≥ {rupees(Number(view.params.large_floor || 0))}, ≥ {(Number(view.params.window_share || 0) * 100).toFixed(0)}% of window volume ·
      clip ≥ {QTY.format(Number(view.params.child_min_qty || 0))} qty and {rupees(Number(view.params.child_floor || 0))} · sensitivity {view.sensitivity}
      {labelled.length ? ` · ${labelled.filter((e) => e.hit).length}/${labelled.length} labelled events went their way` : ""}</p>}
    <div className="whale-tuning-form">
      {Object.keys(form).map((key) => <label key={key}><span>{key.replace(/_/g, " ")}</span>
        <input value={form[key]} placeholder="auto" onChange={(e) => setForm({ ...form, [key]: e.target.value })} /></label>)}
      <button className="of-toggle" onClick={() => save(false)}>Pin override</button>
      <button className="of-toggle" onClick={() => save(true)}>Clear</button>
      <small>{status}</small>
    </div>
    <table className="auction-rate-table"><thead><tr><th>Day</th><th>Prints</th><th>p50</th><th>p90</th><th>p99.9</th><th>Max</th><th>Turnover</th></tr></thead>
      <tbody>{(view?.fingerprints || []).map((f) => <tr key={f.day}><td>{f.day}</td><td>{QTY.format(f.prints)}</td><td>{QTY.format(f.p50)}</td>
        <td>{QTY.format(f.p90)}</td><td>{QTY.format(f.p999)}</td><td>{QTY.format(f.max_qty)}</td><td>{rupees(f.notional)}</td></tr>)}</tbody></table>
    <table className="auction-rate-table"><thead><tr><th>Event</th><th>Features</th><th>P</th><th>30m</th><th>Hit</th></tr></thead>
      <tbody>{(view?.events || []).slice(0, 40).map((e) => <tr key={`${e.day}-${e.at}-${e.kind}`}>
        <td>{e.day.slice(5)} {e.at} {e.kind.replace(/_/g, " ")} {sideLabel(e.side)}</td>
        <td><small>{e.bins.size_vs_p999} p99.9 · {e.bins.window_share} win · {e.bins.turnover_share} t/o · {e.bins.time} · rep {e.bins.repeat}</small></td>
        <td>{e.probability != null ? `${(e.probability * 100).toFixed(0)}%` : "—"}</td>
        <td className={tone(e.ret30)}>{pct(e.ret30)}</td>
        <td>{e.hit == null ? "…" : e.hit ? "✓" : "✗"}</td></tr>)}</tbody></table>
  </aside>;
}


type StudySummary = { latest: null | { study_id: number; since: string; until: string; verdict: string; events: number; symbols: number;
  sessions: number; test?: { hit_rate?: number; n: number }; control_test?: { hit_rate?: number; n: number };
  test_quintiles?: { hit_rate?: number; lift?: number; z?: number | null; n: number }[] };
  studies: { study_id: number; since: string; until: string; verdict: string }[] };

/** The walk-forward edge study of minute-level whale spikes (whale_study.py). */
function WhaleStudyBlock() {
  const study = useEndpoint<StudySummary>("/api/auction/whale/study", []);
  const s = study.data?.latest;
  if (!s) return null;
  return <div className="whale-study">
    <b>Edge study #{s.study_id}</b> <span>{s.since} → {s.until} · {s.events.toLocaleString("en-IN")} spikes · {s.symbols} instruments · {s.sessions} sessions</span>
    <p>{s.verdict}. Spikes {s.test?.hit_rate != null ? `${(s.test.hit_rate * 100).toFixed(1)}%` : "—"} vs ordinary minutes {s.control_test?.hit_rate != null ? `${(s.control_test.hit_rate * 100).toFixed(1)}%` : "—"}.
      Held-out quintiles: {(s.test_quintiles || []).map((q) => q.hit_rate != null ? `${(q.hit_rate * 100).toFixed(0)}%` : "—").join(" → ")}.</p>
  </div>;
}
