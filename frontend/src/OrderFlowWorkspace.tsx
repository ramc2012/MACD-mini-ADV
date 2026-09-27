import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { FootprintChart, type FootprintData } from "./FootprintChart";
import { DomLadder, TimeAndSales } from "./DomLadder";
import { VolumeProfilePane, type ProfileOverlay } from "./VolumeProfilePane";
import type { SessionInfo } from "./types";
import { API_TOKEN, API_URL } from "./runtime";

const headers = API_TOKEN ? { "x-macd-token": API_TOKEN } : undefined;
const money = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const clock = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false,
});
const short = (s: string) => s.split(":")[1] || s;
const words = (s: string | null | undefined) => (s ? s.replace(/_/g, " ") : "—");
const TIMEFRAMES = [60, 180, 300, 900];
const BAR_COUNTS = [20, 40, 80];
const TIMEFRAME_STORE = "macd.flowTimeframe";
const BARS_STORE = "macd.flowBars";
const TPO_STORE = "macd.flowTpo";
// Safari's private mode throws on both halves of localStorage, and a rejected
// preference must not take the workspace down with it.
const recall = (key: string) => { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } };
const remember = (key: string, value: string) => { try { window.localStorage.setItem(key, value); } catch { return; } };
// A remembered choice that is no longer offered (list edited between deploys)
// falls back rather than leaving the toolbar with nothing highlighted.
const recallNumber = (key: string, allowed: number[], fallback: number) =>
  allowed.find((value) => String(value) === recall(key)) ?? fallback;
// Session-seconds advanced per real second while the replay is playing. A full
// NSE session is 22 500 s, so 20x walks it in under twenty minutes and 1x is
// there for the two minutes around an entry that are worth watching honestly.
const SPEEDS = [1, 2, 5, 10, 20];
const COMPOSITES = [0, 3, 5, 20];

/* Reference levels arrive as a flat name -> price map (MarketReference.levels)
   and their names are the only thing that says which session they came from. */
const overlayKind = (name: string): ProfileOverlay["kind"] =>
  name.startsWith("pd_") ? "pd" : name.startsWith("week_") ? "week"
    : name.startsWith("month_") ? "month" : "composite";

export function OrderFlowWorkspace({ symbols, traded = [], focus, onFocus }: {
  symbols: string[]; traded?: string[]; focus: string; onFocus: (s: string) => void;
}) {
  // Any subscribed contract can be opened; a marker shows which have actually
  // printed today, since a chart on an untraded contract is legitimately empty.
  const tradedSet = useMemo(() => new Set(traded), [traded]);
  const [loaded, setLoaded] = useState<{ key: string; value: FootprintData }>();
  const [timeframe, setTimeframe] = useState(() => recallNumber(TIMEFRAME_STORE, TIMEFRAMES, 300));
  const [bars, setBars] = useState(() => recallNumber(BARS_STORE, BAR_COUNTS, 40));
  const [showTpo, setShowTpo] = useState(() => recall(TPO_STORE) === "1");
  const pickTimeframe = (value: number) => { setTimeframe(value); remember(TIMEFRAME_STORE, String(value)); };
  const pickBars = (value: number) => { setBars(value); remember(BARS_STORE, String(value)); };
  const toggleTpo = () => setShowTpo((value) => { remember(TPO_STORE, value ? "0" : "1"); return !value; });
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const [paused, setPaused] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [composite, setComposite] = useState(0);
  const [replay, setReplay] = useState(false);
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [at, setAt] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(5);
  const replayFetch = useRef<AbortController | null>(null);
  const profilePane = useRef<HTMLDivElement>(null);
  const [profileHeight, setProfileHeight] = useState(420);

  // A toolbar or symbol change must not pair an old chart with a new label.
  // The request URL identifies the exact live or replay view on screen.
  const replayPrefix = `${API_URL}/api/mp/replay/${encodeURIComponent(focus)}?day=${encodeURIComponent(day)}&`;
  const requestUrl = replay
    ? `${replayPrefix}at=${at}&bars=${bars}&timeframe_seconds=${timeframe}`
    : `${API_URL}/api/mp/footprint/${encodeURIComponent(focus)}?bars=${bars}&timeframe_seconds=${timeframe}${composite ? `&composite_days=${composite}` : ""}${showTpo ? "&profile_full=true" : ""}`;
  const data = loaded?.key === requestUrl ? loaded.value : undefined;
  const error = failure?.key === requestUrl ? failure.message : "";

  // VolumeProfilePane draws SVG at an explicit height, so measure the pane the
  // grid actually gave us rather than hard-coding a guess.
  useLayoutEffect(() => {
    const node = profilePane.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setProfileHeight(Math.max(160, node.clientHeight)));
    observer.observe(node);
    setProfileHeight(Math.max(160, node.clientHeight));
    return () => observer.disconnect();
  }, []);

  // Only sessions whose raw ticks are still on disk can be replayed; older days
  // were condensed to a ladder and carry no prints to rebuild clusters from.
  useEffect(() => {
    if (!replay || !focus) return;
    let stopped = false;
    void fetch(`${API_URL}/api/mp/replay-days/${encodeURIComponent(focus)}`, { headers })
      .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((list: string[]) => {
        if (stopped) return;
        setDays(list);
        setDay((current) => (current && list.includes(current) ? current : (list[0] || "")));
        setAt(0);
      })
      .catch(() => { if (!stopped) { setDays([]); setDay(""); } });
    return () => { stopped = true; };
  }, [replay, focus]);

  // Keep the selected session's bounds while the next scrub frame loads.
  const replaySession = replay && loaded?.key.startsWith(replayPrefix) ? loaded.value.replay : null;
  const sessionStart = replaySession?.session_start ?? 0;
  const sessionEnd = replaySession?.session_end ?? 0;

  useEffect(() => {
    if (!playing || !replay || !sessionEnd) return;
    const timer = window.setInterval(() => setAt((v) => {
      // Advance only after the current replay frame has arrived. Otherwise a
      // slow response is aborted by the next scrub position on every tick.
      if (replayFetch.current) return v;
      const next = Math.min((v || sessionStart) + speed, sessionEnd);
      if (next >= sessionEnd) setPlaying(false);
      return next;
    }), 1000);
    return () => clearInterval(timer);
  }, [playing, replay, speed, sessionStart, sessionEnd]);

  useEffect(() => {
    if (!focus) return;
    if (replay && !day) return;
    let stopped = false;
    let inFlight: AbortController | null = null;
    const load = () => {
      if ((paused && !replay) || inFlight) return;
      const controller = new AbortController();
      inFlight = controller;
      if (replay) replayFetch.current = controller;
      void fetch(requestUrl, { headers, signal: controller.signal })
        .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then((d) => { if (!stopped) { setLoaded({ key: requestUrl, value: d as FootprintData }); setFailure(undefined); } })
        .catch((e) => { if (!stopped && !controller.signal.aborted) setFailure({
          key: requestUrl, message: e instanceof Error ? e.message : "footprint unavailable",
        }); })
        .finally(() => {
          if (inFlight === controller) inFlight = null;
          if (replayFetch.current === controller) replayFetch.current = null;
        });
    };
    load();
    // A recorded session does not move on its own: it is refetched when the
    // scrubber moves, never on a timer, so playing at 1x costs one request a
    // second rather than one request a second on top of the 3 s live poll.
    if (replay) return () => { stopped = true; inFlight?.abort(); };
    const timer = window.setInterval(load, 3000);
    return () => { stopped = true; clearInterval(timer); inFlight?.abort(); };
  }, [focus, paused, replay, day, requestUrl]);

  const flow = data?.flow;
  const speedRead = data?.tape_speed ?? flow?.tape_speed ?? null;
  const session = data?.session ?? null;
  const totals = useMemo(() => {
    const rows = data?.bars || [];
    const volume = rows.reduce((sum, b) => sum + b.v, 0);
    const delta = rows.reduce((sum, b) => sum + b.delta, 0);
    const imbalanced = rows.reduce((sum, b) => sum + b.levels.filter((l) => l.imb).length, 0);
    return { volume, delta, imbalanced, cells: rows.reduce((s, b) => s + b.levels.length, 0) };
  }, [data]);

  const overlays = useMemo<ProfileOverlay[]>(() => {
    const rows: ProfileOverlay[] = [];
    for (const [name, price] of Object.entries(data?.context?.levels || {})) {
      if (Number.isFinite(price)) rows.push({ label: name.replace(/_/g, " "), price, kind: overlayKind(name) });
    }
    for (const price of data?.context?.naked_pocs || []) {
      if (Number.isFinite(price)) rows.push({ label: "naked POC", price, kind: "naked" });
    }
    const comp = data?.composite;
    if (comp && Number.isFinite(comp.poc)) {
      rows.push({ label: `${comp.days}d POC`, price: comp.poc as number, kind: "composite" });
    }
    if (comp && Number.isFinite(comp.vah)) rows.push({ label: `${comp.days}d VAH`, price: comp.vah as number, kind: "composite" });
    if (comp && Number.isFinite(comp.val)) rows.push({ label: `${comp.days}d VAL`, price: comp.val as number, kind: "composite" });
    return rows;
  }, [data]);

  const profile = data?.profile ?? null;
  const profileSampled = profile?.levels_sampled === true;
  const rowTicks = data && Number.isFinite(data.row_ticks) && data.row_ticks! > 1 ? data.row_ticks! : 1;
  const rowLabel = data ? rowTicks > 1
    ? `aggregated row ₹${data.row_size ?? data.tick_size * rowTicks} (${rowTicks} exchange ticks × ₹${data.tick_size})`
    : `tick ₹${data.tick_size}` : "";

  return <div className="of-workspace">
    <div className="of-toolbar">
      <select className="rrg-select of-symbol" value={focus} onChange={(e) => onFocus(e.target.value)}>
        {!symbols.length && !focus && <option value="">No subscribed symbols</option>}
        {symbols.map((s) => <option key={s} value={s}>{short(s)}{tradedSet.has(s) ? " ·" : ""}</option>)}
        {focus && !symbols.includes(focus) && <option value={focus}>{short(focus)}</option>}
      </select>
      <div className="radar-filter">{TIMEFRAMES.map((tf) => <button key={tf} className={timeframe === tf ? "active" : ""}
        onClick={() => pickTimeframe(tf)}>{tf < 60 ? `${tf}s` : `${tf / 60}m`}</button>)}</div>
      <div className="radar-filter">{BAR_COUNTS.map((n) => <button key={n} className={bars === n ? "active" : ""}
        onClick={() => pickBars(n)}>{n} bars</button>)}</div>
      <button className={showTpo ? "of-toggle active" : "of-toggle"} onClick={toggleTpo}>TPO</button>
      {!replay && <button className={paused ? "of-toggle active" : "of-toggle"} onClick={() => setPaused((v) => !v)}>{paused ? "Paused" : "Live"}</button>}
      <button className={replay ? "of-toggle active" : "of-toggle"}
        onClick={() => { setReplay((v) => !v); setPlaying(false); }}>Replay</button>
      {/* The composite is a trailing window merged per request, so it is asked
          for explicitly: 20 days of ladders is thousands of rows on a poll
          that already runs every three seconds. That is also why it is the one
          toolbar choice not remembered across reloads — reopening the page
          would silently re-arm the expensive request. */}
      <div className="radar-filter">{COMPOSITES.map((n) => <button key={n} className={composite === n ? "active" : ""}
        onClick={() => setComposite(n)}>{n ? `${n}d` : "day"}</button>)}</div>
      <div className="of-stats">
        {/* This is the sum over the BARS ON SCREEN, not session or exchange
            volume — labelled "Volume" it read as the latter and never matched
            the exchange. The window is in the label so the two are not
            confused. */}
        <Stat label={`Window vol · ${bars}×${timeframe < 60 ? `${timeframe}s` : `${timeframe / 60}m`}`}
              value={data ? money.format(totals.volume) : "—"} />
        <Stat label="Bar Δ sum" value={data ? money.format(totals.delta) : "—"}
          tone={data ? totals.delta : undefined} />
        <Stat label="Session CVD" value={Number.isFinite(flow?.cumulative_delta) ? money.format(flow!.cumulative_delta) : "—"}
          tone={Number.isFinite(flow?.cumulative_delta) ? flow!.cumulative_delta : undefined} />
        {/* The same delta with each print weighted by how much the three
            classification votes agreed. Where the two part company the tape
            was batched or contested — which is where the raw one misleads. */}
        {Number.isFinite(flow?.weighted_cumulative_delta) && <Stat label="CVD × conf"
          value={money.format(flow!.weighted_cumulative_delta!)} tone={flow!.weighted_cumulative_delta!} />}
        <Stat label="Imbalance" value={Number.isFinite(flow?.imbalance) ? `${(flow!.imbalance * 100).toFixed(1)}%` : "—"}
          tone={Number.isFinite(flow?.imbalance) ? flow!.imbalance : undefined} />
        <Stat label="Imb. cells" value={data ? `${totals.imbalanced} / ${totals.cells}` : "—"} />
        {/* Speed of tape, ranked against THIS contract's own day. 30 updates a
            second is a dead index future and a frantic weekly option, so the
            absolute figure alone says nothing; the percentile is withheld
            until enough windows have closed today to rank against. */}
        {speedRead && <Stat label={`Tape · ${speedRead.window_seconds}s`}
          value={`${speedRead.updates_per_s.toFixed(1)}/s${Number.isFinite(speedRead.updates_pct) ? ` · P${Math.round(speedRead.updates_pct!)}` : ""}`}
          tone={Number.isFinite(speedRead.updates_pct) ? (speedRead.updates_pct! >= 80 ? 1 : 0) : undefined} />}
        {/* CONFIDENCE. NSE never reports the aggressor, so every delta and
            imbalance above is an inference. These two say how much of the tape
            the desk could actually assign a side to, and on what evidence —
            a 35% quote-share contract and a 95% one are not the same reading,
            and nothing on screen used to distinguish them. */}
        {/* Number.isFinite, not `!== undefined`: the wire sends NULL for a
            share that was never measured, null passes an undefined check, and
            `null * 100` is 0 — so an unmeasured contract rendered a confident
            red "0.0%" that is indistinguishable from a genuinely unclassifiable
            tape. Unmeasured means the stat is absent. */}
        {Number.isFinite(flow?.classified_share) && <Stat
          label="Classified"
          value={`${(flow!.classified_share! * 100).toFixed(1)}%`}
          tone={flow!.classified_share! >= 0.9 ? 1 : flow!.classified_share! >= 0.75 ? 0 : -1} />}
        {Number.isFinite(flow?.quote_share) && <Stat
          label="Quote-ruled"
          value={`${(flow!.quote_share! * 100).toFixed(1)}%`}
          tone={flow!.quote_share! >= 0.5 ? 1 : -1} />}
      </div>
      {data?.setup?.setup && <span className="mp-tag setup" title={data.setup.reason}>{data.setup.setup.replace(/_/g, " ")}</span>}
      {flow?.absorption?.detected && <span className={`mp-tag ${flow.absorption.side === "sellers_absorbed" ? "good" : "bad"}`}>
        {String(flow.absorption.side).replace(/_/g, " ")}</span>}
    </div>

    {replay && <div className="of-scrub">
      <select className="rrg-select" value={day} onChange={(e) => { setDay(e.target.value); setAt(0); setPlaying(false); }}>
        {days.length ? days.map((d) => <option key={d} value={d}>{d}</option>)
          : <option value="">no recorded ticks</option>}
      </select>
      <button className="of-toggle" disabled={!day} onClick={() => setPlaying((p) => !p)}>{playing ? "Pause" : "Play"}</button>
      <div className="radar-filter">{SPEEDS.map((s) => <button key={s} className={speed === s ? "active" : ""}
        onClick={() => setSpeed(s)}>{s}×</button>)}</div>
      <input type="range" min={sessionStart} max={sessionEnd || sessionStart + 1} step={timeframe}
        value={at || sessionStart} disabled={!sessionEnd}
        onChange={(e) => { setPlaying(false); setAt(Number(e.target.value)); }} />
      <span className="of-clock">{at ? clock.format(new Date(at * 1000)) : "--:--"}</span>
      {/* Raw ticks live for tick_retention_days; older sessions were condensed
          to a ladder and carry no prints to rebuild clusters from. */}
      <span className="of-scrub-note">{!days.length ? "no session on this contract still has its raw ticks"
        : data?.replay ? `${data.replay.position} / ${data.replay.ticks} ticks` : "loading session…"}</span>
    </div>}

    {session && <SessionBadges session={session} />}

    {error && <div className="settings-error">{error}</div>}

    <div className={`of-grid${expanded ? " chart-expanded" : ""}`}>
      <section className="panel of-chart">
        <div className="panel-title"><span>Footprint · estimated B/A · {focus ? short(focus) : "—"}</span>
          <span className="of-chart-actions">{data ? `${data.bars.length} ${data.bars.length === 1 ? "bar" : "bars"} · ${rowLabel} · imbalance ≥${data.imbalance_ratio}×` : ""}
            <button onClick={() => setExpanded((value) => !value)}>{expanded ? "Exit expanded" : "Expand chart"}</button></span></div>
        <div className="of-chart-body">
          {data && data.bars.length > 0
            ? <FootprintChart data={data} height="100%" requestedBars={bars} />
            : <div className="chart-empty">{!focus
                ? "No symbol selected. Check the broker connection and subscriptions, then choose a contract."
                : replay && !days.length
                ? "No recorded session for this contract — raw ticks are kept for a few days only."
                : !data ? "Loading clusters…"
                : replay ? "Nothing had printed yet at this point in the session."
                : tradedSet.has(focus) ? "No prints captured for this contract yet — clusters build from live trades."
                : "This contract has not traded today, so there is no auction to draw yet."}</div>}
        </div>
      </section>

      <section className="panel of-profile">
        <div className="panel-title"><span>Session profile</span>
          <span>{data?.composite ? `${showTpo ? "TPO + volume" : "volume"} · ${data.composite.days}/${data.composite.requested_days}d composite`
            : profileSampled ? `volume · sampled ${profile!.levels_returned ?? profile!.levels.length}/${profile!.levels_total ?? "?"} levels`
              : showTpo ? "TPO + volume" : "volume"}</span></div>
        <div className="of-profile-body" ref={profilePane}>
          <VolumeProfilePane profile={profile} showTpo={showTpo} height={profileHeight}
            overlays={overlays}
            completedVa={session?.prior_day ? { vah: session.prior_day.vah, val: session.prior_day.val, label: "PD VA" } : null}
            vpoc={profile?.vpoc ?? null}
            singlePrints={profile?.single_prints}
            poorHigh={session?.poor_high ?? profile?.poor_high ?? false}
            poorLow={session?.poor_low ?? profile?.poor_low ?? false}
            tailHigh={session?.tail_high ?? profile?.tail_high}
            tailLow={session?.tail_low ?? profile?.tail_low} />
        </div>
      </section>

      <section className="panel of-ladder">
        <div className="panel-title"><span>DOM</span><span>touch only</span></div>
        <DomLadder dom={data?.dom ?? null} profile={data?.profile ?? null}
          tickSize={data?.tick_size ?? 0.05} lastPrice={data?.dom?.last ?? data?.profile?.last ?? null} />
      </section>

      <section className="panel of-tape">
        <div className="panel-title"><span>Time &amp; sales</span><span>{data?.tape?.length ?? 0}</span></div>
        <TimeAndSales tape={data?.tape ?? []} tickSize={data?.tick_size ?? 0.05} />
      </section>
    </div>
  </div>;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return <div className="of-stat"><span>{label}</span>
    <b className={tone === undefined ? "" : tone >= 0 ? "positive" : "negative"}>{value}</b></div>;
}

/**
 * What the reader has to know about the day before reading a single cluster.
 *
 * The two day types are shown side by side under their own names rather than
 * merged: "IB" reads range extension past the initial balance and moves during
 * the session, "VA" reads value-area coverage and is the taxonomy the stored
 * base rates are keyed on. A single blended word would answer neither reader.
 */
export function SessionBadges({ session }: { session: SessionInfo }) {
  const value = session.value_relationship || "";
  const grade = session.quality?.grade ?? null;
  return <div className="of-badges">
    <Badge label="open" value={words(session.open_type)}
      title={session.open_observed ? "classified from the observed open window"
        : "the open window was not captured — restored or late-joined session"}
      tone={session.open_observed ? "" : "muted"} />
    {session.open_location && <Badge label="vs prior" value={words(session.open_location)} />}
    <Badge label={`day IB${session.day_type_bracket ? ` (${session.day_type_bracket})` : ""}`}
      value={words(session.day_type_ib)}
      title="live: range extension beyond the initial balance" />
    <Badge label="day VA" value={words(session.day_type_va)}
      title="stored taxonomy: how much of the range the value area covers — the one base rates are keyed on" />
    <Badge label="IB" value={session.partial_capture ? "unobserved"
      : session.ib_high !== null && session.ib_low !== null
        ? `${session.ib_low}–${session.ib_high}` : "forming"}
      tone={session.ib_complete ? "" : "muted"}
      title={session.partial_capture ? "the 09:15–10:15 initial balance was missed"
        : session.ib_complete ? "initial balance complete" : "first hour still forming"} />
    <Badge label="ext" value={Number.isFinite(session.extension?.ratio)
      ? `${Math.round((session.extension.ratio as number) * 100)}% IB` : "none"} />
    {/* Both of these describe a comparison with the PRIOR session, and both
        are absent — not "unknown" — when no prior session is stored for this
        underlying. A badge reading "value unknown" every session claims a
        measurement was taken and came back empty; nothing was measured. */}
    {session.prior_day_date && <Badge label="value" value={words(value)}
      tone={value.startsWith("higher") ? "good" : value.startsWith("lower") ? "bad" : ""}
      title={`against ${session.prior_day_date}`} />}
    {/* The VIX range is a one-day move implied by the index, taken around the
        UNDERLYING's price — an option premium's own ±points would be a
        different quantity wearing the same label. Drawn only once a VIX tick
        has arrived; the index has to be subscribed for that, and a permanent
        "n/a" told the reader nothing except that it is not. */}
    {session.vix_implied_range !== null && session.vix_implied_range !== undefined
      && <Badge label="VIX rng" value={`±${session.vix_implied_range.toFixed(0)}`}
        title={`India VIX ${session.vix} around ${session.vix_reference_price}`} />}
    {session.expiry_day && <Badge label="expiry" value={session.expiring ? "this contract" : "today"} tone="bad" />}
    <Badge label="regime" value={session.regime_id} />
    {(session.poor_high || session.poor_low) && <Badge label="poor"
      value={[session.poor_high ? "high" : "", session.poor_low ? "low" : ""].filter(Boolean).join(" + ")}
      tone="bad" title="extreme with no excess — Dalton's reading is that it gets revisited" />}
    {(session.tail_high >= 2 || session.tail_low >= 2) && <Badge label="tail"
      value={[session.tail_high >= 2 ? `hi ${session.tail_high}` : "", session.tail_low >= 2 ? `lo ${session.tail_low}` : ""].filter(Boolean).join(" · ")} />}
    <Badge label="quality" value={grade ? `${grade}${Number.isFinite(session.quality.quote_share)
      ? ` · q${Math.round(session.quality.quote_share! * 100)}%` : ""}` : "unmeasured"}
      tone={grade === "high" ? "good" : grade === "low" ? "bad" : "muted"}
      title={`${session.quality.prints} prints classified`} />
  </div>;
}

function Badge({ label, value, tone, title }: {
  label: string; value: string; tone?: string; title?: string;
}) {
  return <span className={`mp-tag${tone ? ` ${tone}` : ""}`} title={title || label}>
    <i>{label}</i>{value}</span>;
}
