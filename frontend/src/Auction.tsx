import { ColorType, createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
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

export function AuctionPage() {
  const { data: symbols } = useEndpoint<string[]>("/api/auction/symbols", []);
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
  const [flowDay, setFlowDay] = useState("");
  const flow = useEndpoint<AuctionFlow>(
    symbol ? `/api/auction/flow/${encodeURIComponent(symbol)}${flowDay ? `?day=${flowDay}` : ""}` : null, [symbol, flowDay]);

  const regimeId = context.data?.regime.regime_id;
  const journal = useEndpoint<SetupJournal>(
    symbol ? `/api/auction/setups/${encodeURIComponent(symbol)}${regimeId ? `?regime_id=${regimeId}` : ""}` : null, [symbol, regimeId]);
  // Windows land once a minute; fetched on symbol change alone, the panel would show the open all day.
  const [minute, setMinute] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setMinute((count) => count + 1), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const whale = useEndpoint<WhaleView>("/api/auction/whale", [symbol, minute]);

  const reference = context.data;
  return <section className="ledger-page auction-page">
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
      <LocationPanel reference={reference} />
      <LevelsPanel reference={reference} />
      <TodayPanel rates={rates.data} />
      <FlowPanel flow={flow.data} loading={flow.loading} onDay={setFlowDay} day={flowDay} />
      <BaseRatePanel rates={rates.data} error={rates.error} />
      <SetupPanel journal={journal.data} />
      <WhalePanel view={whale.data} />
    </div>}
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
    <p className="auction-note">An untested point of control stays a magnet until price trades
      back through it. These are the ones no later session has reached.</p>
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
    {!series.length && <div className="chart-empty">{loading ? "Loading session flow…" : "No condensed flow for this session yet."}</div>}
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
