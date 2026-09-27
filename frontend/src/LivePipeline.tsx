import { useEffect, useState } from "react";
import { API_TOKEN, API_URL } from "./runtime";
import { requestJson } from "./requestJson";

// Live analytics from the tick bus: the Go gateway publishes each engine tick
// to NATS, and Rust worker shards build 1-minute bars, MACD and volatility per
// symbol in parallel. Polled every 2 s while the Quant page is open.

type Seed = { state: "unrequested" | "pending" | "seeded" | "failed"; bars?: number; error?: string };
type Bar = { start_ms: number; open: number; high: number; low: number; close: number; volume: number; ticks: number };
export type LiveView = {
  symbol: string; ltp: number; exchange_ts_ms: number; ticks: number; late_ticks: number; off_session_ticks: number; stale_ticks: number;
  bar: Bar | null; bars_closed: number; ema_fast: number | null; ema_slow: number | null; macd: number | null;
  signal: number | null; histogram: number | null; realized_volatility_pct: number | null;
  trend: "bullish" | "bearish" | "neutral" | "no_data"; warm: boolean; seed: Seed;
  periods: { fast: number; slow: number; signal: number };
};
type LiveStats = {
  workers: number; shards: { symbols: number; ticks: number }[];
  bus: { connected: boolean; messages: number; dropped: number; parse_errors: number };
  bars_closed: number;
  seeds: { by_state: Record<string, number>; failed: number };
  questdb: { connected: boolean; tables_ready: boolean; lines: number; dropped: number };
  last_error: string;
};
type StreamStats = {
  mode: "fanout" | "tunnel";
  upstream?: { connected: boolean; events: number; gaps: number; snapshot_bytes: number; snapshots_fetched: number };
  clients?: { queued: number; sent: number; coalesced: number; resyncs: number }[];
  bus?: { enabled: boolean; published: number; errors: number };
};
// The engine publishes ticks to the bus itself (it did through the gateway
// before the engine split); its counters come with the system health.
type EngineBus = { layout?: { role?: string; bus?: { connected: boolean; published: number; dropped: number } | null } };
type Sort = "abs_histogram" | "histogram" | "volatility" | "ticks";

const count = new Intl.NumberFormat("en-IN");
const price = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const small = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 4 });
const fmt = (value: number | null | undefined, format = small) =>
  value === null || value === undefined || !Number.isFinite(value) ? "—" : format.format(value);
const tone = (value: number | null | undefined) => value === null || value === undefined ? "" : value >= 0 ? "positive" : "negative";

async function optional<T>(url: string, headers: HeadersInit | undefined, signal: AbortSignal): Promise<T | undefined> {
  try {
    return await requestJson<T>(url, { headers, signal });
  } catch {
    return undefined;
  }
}

export function LivePipeline({ symbol, onSelect }: { symbol: string; onSelect: (symbol: string) => void }) {
  const [sort, setSort] = useState<Sort>("abs_histogram");
  const [rows, setRows] = useState<LiveView[]>([]);
  const [focus, setFocus] = useState<LiveView | undefined>();
  const [stats, setStats] = useState<LiveStats | undefined>();
  const [stream, setStream] = useState<StreamStats | undefined>();
  const [engineBus, setEngineBus] = useState<EngineBus["layout"]>();
  const [unavailable, setUnavailable] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
    let timer: number | undefined;
    const poll = async () => {
      clearTimeout(timer);
      if (!document.hidden) {
        const [scan, live, gateway, selected, system] = await Promise.all([
          optional<{ rows: LiveView[] }>(`${API_URL}/parallel/live/scan?sort=${sort}&limit=25`, headers, controller.signal),
          optional<LiveStats>(`${API_URL}/parallel/live/stats`, headers, controller.signal),
          optional<StreamStats>(`${API_URL}/parallel/stream/stats`, headers, controller.signal),
          symbol ? optional<LiveView>(`${API_URL}/parallel/live?symbol=${encodeURIComponent(symbol)}`, headers, controller.signal) : Promise.resolve(undefined),
          optional<EngineBus>(`${API_URL}/api/system/health`, headers, controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setUnavailable(!live);
        setRows(scan?.rows || []);
        setStats(live);
        setStream(gateway);
        setFocus(selected);
        setEngineBus(system?.layout);
        setLoaded(true);
      }
      timer = window.setTimeout(poll, 2000);
    };
    // A hidden tab skips polls; catch up the moment it is shown again.
    const onVisible = () => { if (!document.hidden) void poll(); };
    document.addEventListener("visibilitychange", onVisible);
    void poll();
    return () => { controller.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [symbol, sort]);

  const shardTicks = stats?.shards.reduce((sum, shard) => sum + shard.ticks, 0) || 0;
  const coalesced = stream?.clients?.reduce((sum, client) => sum + client.coalesced, 0) || 0;
  const resyncs = stream?.clients?.reduce((sum, client) => sum + client.resyncs, 0) || 0;
  // Whoever publishes: the engine (split layout) or the gateway (older layout).
  const publisher = engineBus?.bus ? { name: "engine", published: engineBus.bus.published, dropped: engineBus.bus.dropped, ok: engineBus.bus.connected }
    : { name: "gateway", published: stream?.bus?.published || 0, dropped: stream?.bus?.errors || 0, ok: (stream?.bus?.errors || 0) === 0 };

  return <section className="live-pipeline">
    <div className="live-heading">
      <div>
        <h2>Live pipeline</h2>
        <p>Engine ticks fanned out by the Go gateway and published to the NATS bus; Rust worker shards compute 1-minute bars, MACD and volatility per symbol in parallel.</p>
      </div>
    </div>

    {unavailable ? <div className="quant-message panel" role="status">
      <strong>Live analytics unavailable</strong><span>The analytics service has no tick bus configured, or is not reachable.</span>
    </div> : <>
      <div className="live-stages">
        <Stage title="Gateway fan-out" loaded={loaded} ok={stream?.mode === "fanout" && !!stream.upstream?.connected}
          lines={stream?.mode === "fanout" ? [
            `${count.format(stream.clients?.length || 0)} browser${stream.clients?.length === 1 ? "" : "s"} · ${count.format(stream.upstream?.events || 0)} events`,
            `${count.format(coalesced)} superseded ticks coalesced · ${count.format(resyncs)} resyncs`,
            `${count.format(stream.upstream?.gaps || 0)} engine gaps · snapshot ${fmt((stream.upstream?.snapshot_bytes || 0) / 1024 / 1024, price)} MB`,
          ] : ["tunnelled directly to the engine"]} />
        <Stage title="Tick bus" loaded={loaded} ok={!!stats?.bus.connected && publisher.ok}
          lines={[
            `${count.format(publisher.published)} published by the ${publisher.name} · ${count.format(stats?.bus.messages || 0)} received`,
            `${count.format(publisher.dropped)} not published`,
            `${count.format(stats?.bus.dropped || 0)} dropped at shards · ${count.format(stats?.bus.parse_errors || 0)} unreadable`,
          ]} />
        <Stage title={stats ? `${stats.workers} worker shards` : "Worker shards"} loaded={loaded} ok={!!stats}
          lines={[
            `${count.format(shardTicks)} ticks · ${count.format(stats?.bars_closed || 0)} bars closed`,
            stats ? stats.shards.map((shard) => count.format(shard.symbols)).join(" / ") + " symbols per shard" : "—",
            `seeded ${count.format(stats?.seeds.by_state.seeded || 0)} · pending ${count.format(stats?.seeds.by_state.pending || 0)} · failed ${count.format(stats?.seeds.by_state.failed || 0)}`,
          ]} />
        <Stage title="QuestDB" loaded={loaded} ok={!!stats?.questdb.connected && !!stats.questdb.tables_ready}
          lines={[
            `${count.format(stats?.questdb.lines || 0)} lines written`,
            `${count.format(stats?.questdb.dropped || 0)} dropped · tables ${stats?.questdb.tables_ready ? "ready" : "not ready"}`,
          ]} />
      </div>
      {stats?.last_error && <p className="quant-note">Last pipeline error: {stats.last_error}</p>}

      <div className="live-focus panel">
        <div className="live-focus-head">
          <strong>{symbol || "No instrument"}</strong>
          {focus ? <span className={`live-badge ${focus.warm ? "warm" : ""}`}>{focus.warm ? "warm" : `warming · ${focus.bars_closed}/${focus.periods.slow + focus.periods.signal} bars`}</span>
            : <span className="live-badge">no live ticks yet</span>}
        </div>
        {focus && <div className="live-focus-grid">
          <Cell label="LTP" value={fmt(focus.ltp, price)} />
          <Cell label="Forming bar" value={focus.bar ? `${fmt(focus.bar.open, price)} → ${fmt(focus.bar.close, price)}` : "—"} />
          <Cell label={`MACD ${focus.periods.fast}/${focus.periods.slow}`} value={fmt(focus.macd)} tone={tone(focus.macd)} />
          <Cell label={`Signal ${focus.periods.signal}`} value={fmt(focus.signal)} />
          <Cell label="Histogram" value={fmt(focus.histogram)} tone={tone(focus.histogram)} />
          <Cell label="Realized vol (2h)" value={focus.realized_volatility_pct === null ? "—" : `${fmt(focus.realized_volatility_pct, price)}%`} />
          <Cell label="Ticks · stale · late · off-hours" value={`${count.format(focus.ticks)} · ${count.format(focus.stale_ticks)} · ${count.format(focus.late_ticks)} · ${count.format(focus.off_session_ticks)}`} />
          <Cell label="History seed" value={focus.seed.state === "seeded" ? `${count.format(focus.seed.bars || 0)} bars` : focus.seed.state === "failed" ? `failed: ${focus.seed.error}` : focus.seed.state} />
        </div>}
      </div>

      <div className="live-scan panel">
        <div className="live-scan-head">
          <strong>Scanner</strong>
          <label htmlFor="live-sort">Rank by</label>
          <select id="live-sort" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
            <option value="abs_histogram">|Histogram|</option>
            <option value="histogram">Histogram</option>
            <option value="volatility">Realized volatility</option>
            <option value="ticks">Tick count</option>
          </select>
        </div>
        {rows.length === 0 ? <p className="quant-note">No live symbols yet. Rows appear as ticks arrive during the session.</p>
          : <div className="live-table-wrap"><table className="live-table">
            <thead><tr><th>Symbol</th><th>LTP</th><th>MACD</th><th>Histogram</th><th>Vol %</th><th>Bars</th><th>Ticks</th></tr></thead>
            <tbody>{rows.map((row) => <tr key={row.symbol} className={row.symbol === symbol ? "selected" : ""} onClick={() => onSelect(row.symbol)}>
              <td title={row.symbol}>{row.symbol}{row.warm ? "" : " ·"}</td>
              <td>{fmt(row.ltp, price)}</td>
              <td className={tone(row.macd)}>{fmt(row.macd)}</td>
              <td className={tone(row.histogram)}>{fmt(row.histogram)}</td>
              <td>{fmt(row.realized_volatility_pct, price)}</td>
              <td>{count.format(row.bars_closed)}</td>
              <td>{count.format(row.ticks)}</td>
            </tr>)}</tbody>
          </table></div>}
        <p className="quant-note">“·” marks a symbol whose MACD is still warming. Analysis-leg quotes reach the bus at the engine’s rationed rate.</p>
      </div>
    </>}
  </section>;
}

function Stage({ title, ok, loaded, lines }: { title: string; ok: boolean; loaded: boolean; lines: string[] }) {
  // Grey until the first poll answers: no data is not the same as unhealthy.
  const state = !loaded ? "" : ok ? "ok" : "bad";
  return <div className="live-stage panel">
    <span className={`live-dot ${state}`} aria-label={!loaded ? "checking" : ok ? "healthy" : "not healthy"} />
    <strong>{title}</strong>
    {loaded ? lines.map((line) => <small key={line}>{line}</small>) : <small>checking…</small>}
  </div>;
}

function Cell({ label, value, tone: toneClass }: { label: string; value: string; tone?: string }) {
  return <div><span>{label}</span><b className={toneClass}>{value}</b></div>;
}
