"use client";

/**
 * The market view — every symbol Vanguard evaluated, with what it collected.
 *
 * This tab exists because the desk could previously show that the lane decided
 * nothing but not what it decided nothing ABOUT: not one symbol's collected
 * market information was visible anywhere in the UI. The funnel said "180 died
 * at flow_fresh"; nothing said WHICH 180, what their flow scores were, or how
 * old.
 *
 * Every numeric input is rendered next to the AGE of the row it came from,
 * because the lane's most consequential defect was a set of joins with no
 * maximum age — a month-old flow score entering a live evaluation as though it
 * were yesterday's. A UI that shows the number without its age reproduces
 * exactly that blindness for the human reader.
 *
 * Sorting defaults to conviction, but the useful default filter is "died at" —
 * pick a leg and the grid becomes the population that leg is killing.
 */
import { useMemo, useState } from "react";
import { Activity, Layers, Search, X } from "lucide-react";

import { MetricTile, Section, StatusBadge, formatIST } from "@/components/desk-ui";
import {
  GexScale,
  IvCell,
  IvsCell,
  LEG_LABELS,
  LEG_ORDER,
  LegChain,
  MwplCell,
  OiCell,
  OiStateBadge,
  PcrCell,
  PerfCell,
  ScoreBar,
  SortHeader,
  SkewCell,
  Spark,
  TimingChip,
  Unmeasured,
  ValueAge,
  ValueAreaGauge,
  num,
} from "./vanguard-vocab";

/**
 * Three lenses over one row set.
 *
 * Everything the lane collects for a symbol is now ~22 columns, which is not a
 * table anyone can read. Rather than hiding data behind a horizontal scrollbar,
 * the columns are grouped by the QUESTION they answer, with symbol / legs /
 * conviction anchored in every lens so a row never loses its identity or its
 * verdict when the lens changes.
 *
 *   decision      what M6 was given and what it did with it
 *   positioning   open interest, the OI/price conjunction, PCR, ban proximity
 *   performance   price and returns
 */
const LENSES = {
  decision: "Decision inputs",
  positioning: "Positioning & OI",
  volatility: "Implied volatility",
  performance: "Price performance",
} as const;
type Lens = keyof typeof LENSES;

/**
 * Sort keys map to a NUMBER per row. Everything sorts descending-first because
 * every column here is "more is more interesting"; nulls always sink, so a
 * missing reading can never masquerade as an extreme one.
 */
const SORTS: Record<string, (r: any) => number | null> = {
  conviction: (r) => num(r.conviction),
  flow: (r) => { const v = num(r.flow_score); return v == null ? null : Math.abs(v); },
  flow_age: (r) => num(r.flow_age_sessions),
  ingredients: (r) => num(r.flow_n_ingredients),
  rs: (r) => { const v = num(r.rs_z20); return v == null ? null : Math.abs(v); },
  gex: (r) => num(r.gex_percentile),
  timing: (r) => num(r.timing_score),
  rvol: (r) => num(r.rvol),
  va: (r) => num(r.va_position),
  oi: (r) => num(r.total_oi),
  d_oi: (r) => num(r.d_oi_pct),
  oi_strength: (r) => num(r.oi_state_strength),
  pcr: (r) => num(r.oi_pcr),
  mwpl: (r) => num(r.mwpl_pct),
  close: (r) => num(r.close),
  d_price: (r) => num(r.d_price_pct),
  ret_5d: (r) => num(r.ret_5d),
  ret_20d: (r) => num(r.ret_20d),
  ret_60d: (r) => num(r.ret_60d),
  atm_iv: (r) => num(r.atm_iv),
  iv_percentile: (r) => num(r.iv_percentile),
  d_atm_iv: (r) => num(r.d_atm_iv),
  ivs: (r) => { const v = num(r.ivs); return v == null ? null : Math.abs(v); },
  skew: (r) => num(r.skew_25d),
  iv_strikes: (r) => num(r.iv_n_strikes),
};

const DEFAULT_SORT: Record<Lens, string> = {
  decision: "conviction",
  positioning: "oi_strength",
  performance: "d_price",
  volatility: "iv_percentile",
};

/** Which sort keys each lens actually shows a column for. */
const LENS_SORTS: Record<Lens, string[]> = {
  decision: ["conviction", "flow", "flow_age", "ingredients", "rs", "gex", "timing", "rvol", "va", "symbol"],
  positioning: ["conviction", "oi", "d_oi", "oi_strength", "pcr", "mwpl", "gex", "symbol"],
  performance: ["conviction", "close", "d_price", "ret_5d", "ret_20d", "ret_60d", "rvol", "symbol"],
  volatility: ["conviction", "atm_iv", "iv_percentile", "d_atm_iv", "ivs", "skew", "iv_strikes", "symbol"],
};

export function MarketTab({
  market,
  selectedSymbol,
  onPickSymbol,
}: {
  market?: any;
  selectedSymbol?: string | null;
  onPickSymbol?: (symbol: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [diedAt, setDiedAt] = useState<string>("");
  const [sector, setSector] = useState<string>("");
  const [lens, setLens] = useState<Lens>("decision");
  const [sort, setSort] = useState<string>(DEFAULT_SORT.decision);
  const [oiState, setOiState] = useState<string>("");

  const pickLens = (next: Lens) => {
    setLens(next);
    // A lens change moves the sort to that lens's own default only if the
    // current sort column is not visible in the new lens — otherwise a user who
    // deliberately sorted by conviction loses it just by looking at OI.
    if (!SORTS[sort] || !LENS_SORTS[next].includes(sort)) setSort(DEFAULT_SORT[next]);
  };

  const rows: any[] = market?.symbols ?? [];
  const thresholds = market?.thresholds ?? {};
  const coverage = market?.coverage ?? {};

  const sectors = useMemo(
    () => Array.from(new Set(rows.map((r) => r.sector20).filter(Boolean))).sort(),
    [rows],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    let out = rows;
    if (q) out = out.filter((r) => String(r.symbol).includes(q));
    if (sector) out = out.filter((r) => r.sector20 === sector);
    if (diedAt === "__survived") out = out.filter((r) => r.survived_filter);
    else if (diedAt) out = out.filter((r) => !r.survived_filter && r.first_failed_leg === diedAt);
    if (oiState) out = out.filter((r) => r.oi_state === oiState);

    if (sort === "symbol") {
      return [...out].sort((a, b) => String(a.symbol).localeCompare(String(b.symbol)));
    }
    const key = SORTS[sort] ?? SORTS.conviction;
    // Nulls sink regardless of direction: an unmeasured value must never sort
    // as though it were an extreme one.
    return [...out].sort((a, b) => {
      const av = key(a);
      const bv = key(b);
      if (av == null && bv == null) return String(a.symbol).localeCompare(String(b.symbol));
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    });
  }, [rows, query, sector, diedAt, oiState, sort]);

  const oiCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    rows.forEach((r) => {
      if (r.oi_state) counts[r.oi_state] = (counts[r.oi_state] ?? 0) + 1;
    });
    return counts;
  }, [rows]);

  // Deaths per leg, computed from the same rows the grid renders, so the chips
  // and the table can never disagree about who died where.
  const deaths = useMemo(() => {
    const counts: Record<string, number> = {};
    rows.forEach((r) => {
      if (!r.survived_filter && r.first_failed_leg) counts[r.first_failed_leg] = (counts[r.first_failed_leg] ?? 0) + 1;
    });
    return counts;
  }, [rows]);

  if (market?.unavailable) {
    return (
      <Section title="Market view unavailable" icon={<Layers size={16} />}>
        <p className="text-sm text-text-secondary">{market.unavailable}</p>
        <p className="mt-2 text-[11px] text-text-muted">
          This is a missing table, not an empty market. Nothing is inferred from it.
        </p>
      </Section>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <MetricTile label="evaluated" value={String(coverage.evaluated ?? 0)}
                    detail="symbols with a timing bar" />
        <MetricTile label="with flow" value={String(coverage.with_flow ?? 0)}
                    detail="M2 wrote something" />
        <MetricTile
          label="fresh flow"
          value={String(coverage.with_fresh_flow ?? 0)}
          detail={`≤ ${thresholds.flow_max_age_sessions ?? "?"} sessions old`}
          color={(coverage.with_fresh_flow ?? 0) === 0 ? "text-accent-amber" : undefined}
        />
        <MetricTile label="with regime" value={String(coverage.with_regime ?? 0)} detail="M3 GEX bucket" />
        <MetricTile label="sector RS" value={String(coverage.with_sector_rs ?? 0)} detail="M4 z-score" />
        <MetricTile label="igniting" value={String(coverage.igniting ?? 0)} detail="M5 IGNITION state" />
        <MetricTile
          label="survivors"
          value={String(coverage.survivors ?? 0)}
          detail="admitted by M6"
          color={(coverage.survivors ?? 0) > 0 ? "text-accent-green" : undefined}
        />
        <MetricTile label="with OI" value={String(coverage.with_oi ?? 0)}
                    detail={coverage.oi_session ? `as of ${String(coverage.oi_session)}` : "no OI session"} />
        <MetricTile label="positioning" value={String(coverage.with_oi_state ?? 0)}
                    detail="OI × price conjunction" />
        <MetricTile label="with IV" value={String(coverage.with_iv ?? 0)}
                    detail="solved, not vendor-supplied" />
        <MetricTile label="with IVS" value={String(coverage.with_ivs ?? 0)}
                    detail="call−put spread computable" />
        <MetricTile
          label="with 25Δ skew"
          value={String(coverage.with_skew ?? 0)}
          detail="chain reaches the wings"
          color={(coverage.with_skew ?? 0) === 0 ? "text-accent-amber" : undefined}
        />
        <MetricTile
          label="at MWPL limit"
          value={String(coverage.at_mwpl_limit ?? 0)}
          detail="≥95% — NSE bans fresh F&O"
          color={(coverage.at_mwpl_limit ?? 0) > 0 ? "text-accent-red" : undefined}
        />
      </div>

      {(coverage.at_mwpl_limit ?? 0) > 0 && (
        <div className="rounded-xl border border-accent-red/35 bg-accent-red/8 px-3 py-2 text-[11px] leading-relaxed text-text-secondary">
          <strong className="text-accent-red">
            {coverage.at_mwpl_limit} symbol{(coverage.at_mwpl_limit ?? 0) === 1 ? " is" : "s are"} at or past 95% MWPL utilisation
          </strong>{" "}
          — NSE bans fresh F&amp;O positions in those names. <strong>M7 does not veto on this.</strong> It
          can still size a banned name; the MWPL column is the only control. Sort by MWPL in the
          Positioning lens to see them.
        </div>
      )}

      {coverage.evaluated > 0 && (coverage.with_fresh_flow ?? 0) === 0 && (
        <div className="rounded-xl border border-accent-amber/35 bg-accent-amber/8 px-3 py-2 text-[11px] leading-relaxed text-text-secondary">
          <strong className="text-accent-amber">No symbol has a fresh flow score at this bar.</strong>{" "}
          Flow ages between {coverage.min_flow_age ?? "?"} and {coverage.max_flow_age ?? "?"} sessions
          against a {thresholds.flow_max_age_sessions ?? "?"}-session limit. Until an IV feed
          returns, the lane cannot emit — and that is a DATA state, not a threshold to relax.
          Every other column below is still live and still worth reading.
        </div>
      )}

      <Section
        title="Collected market information, per symbol"
        icon={<Activity size={16} />}
        description={
          market?.ts
            ? `${formatIST(market.ts)} · ${filtered.length} of ${rows.length} symbols · every input shown with the age of the row it came from`
            : "No evaluated bar yet."
        }
        rightSlot={
          <div className="flex flex-wrap items-center gap-1.5">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="symbol"
                className="w-28 rounded-lg border border-bg-border bg-bg-primary/40 py-1 pl-6 pr-2 font-mono text-[11px] text-text-primary outline-none focus:border-accent-blue/50"
              />
            </div>
            <select
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              className="rounded-lg border border-bg-border bg-bg-primary/40 px-2 py-1 text-[11px] text-text-secondary outline-none"
            >
              <option value="">all sectors</option>
              {sectors.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>

          </div>
        }
      >
        <div className="mb-2.5 flex flex-wrap items-center gap-1">
          {(Object.keys(LENSES) as Lens[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => pickLens(key)}
              className={
                "rounded-lg border px-2.5 py-1 text-[11px] transition-colors " +
                (lens === key
                  ? "border-accent-blue/50 bg-accent-blue/12 text-text-primary"
                  : "border-bg-border text-text-muted hover:bg-bg-hover/30")
              }
            >
              {LENSES[key]}
            </button>
          ))}
          <span className="ml-2 text-[10px] text-text-muted">
            same {rows.length} rows, different columns · click any header to sort
          </span>
        </div>

        {lens !== "decision" && (
          <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-[0.14em] text-text-muted">buildup</span>
            <FilterChip active={oiState === ""} onClick={() => setOiState("")}
                        label={`any · ${rows.length}`} />
            {[
              ["long_buildup", "long build", "green"],
              ["short_covering", "short cover", "green"],
              ["short_buildup", "short build", "red"],
              ["long_unwind", "long unwind", "red"],
            ].map(([key, label, tone]) => (
              <FilterChip
                key={key}
                active={oiState === key}
                onClick={() => setOiState(oiState === key ? "" : key)}
                label={`${label} · ${oiCounts[key as string] ?? 0}`}
                tone={(oiCounts[key as string] ? tone : "muted") as "muted" | "red" | "green"}
              />
            ))}
          </div>
        )}

        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-[0.14em] text-text-muted">rejected at</span>
          <FilterChip active={diedAt === ""} onClick={() => setDiedAt("")}
                      label={`all · ${rows.length}`} />
          <FilterChip
            active={diedAt === "__survived"}
            onClick={() => setDiedAt("__survived")}
            label={`admitted · ${coverage.survivors ?? 0}`}
            tone="green"
          />
          {LEG_ORDER.map((leg) => (
            <FilterChip
              key={leg}
              active={diedAt === leg}
              onClick={() => setDiedAt(diedAt === leg ? "" : leg)}
              label={`${LEG_LABELS[leg]} · ${deaths[leg] ?? 0}`}
              tone={deaths[leg] ? "red" : "muted"}
            />
          ))}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs" style={{ minWidth: lens === "decision" ? 1040 : 900 }}>
            <thead className="sticky top-0 z-10 bg-bg-secondary/80 text-[10px] text-text-muted backdrop-blur">
              <tr>
                <SortHeader label="symbol" sortKey="symbol" active={sort} onSort={setSort} />
                <th className="py-2 pr-3 uppercase tracking-[0.14em]"
                    title="Independent leg opinions: green supports, amber disagrees, hollow unavailable; red marks a recorded rejection.">
                  legs
                </th>

                {lens === "decision" && (
                  <>
                    <SortHeader label="flow · age" sortKey="flow" active={sort} onSort={setSort}
                      title="M2 informed-flow composite, and how many sessions old that reading is. Sorts by |flow|." />
                    <SortHeader label="ing" sortKey="ingredients" active={sort} onSort={setSort}
                      title="How many of the five ingredients contributed. A ±100 from one ingredient is not the same reading as one from five." />
                    <SortHeader label="sector RS · age" sortKey="rs" active={sort} onSort={setSort} />
                    <SortHeader label="GEX regime" sortKey="gex" active={sort} onSort={setSort}
                      title="Dealer gamma percentile against this symbol's own trailing 60 sessions. Teal = short gamma (M6 permits), amber = long gamma (M6 blocks)." />
                    <SortHeader label="timing" sortKey="timing" active={sort} onSort={setSort} />
                    <SortHeader label="RVOL" sortKey="rvol" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="value area" sortKey="va" active={sort} onSort={setSort} />
                  </>
                )}

                {lens === "positioning" && (
                  <>
                    <SortHeader label="open interest" sortKey="oi" active={sort} onSort={setSort}
                      title="Aggregate F&O open interest. '~' marks a row summed from collected contracts rather than NSE's own publication." />
                    <SortHeader label="ΔOI" sortKey="d_oi" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="buildup" sortKey="oi_strength" active={sort} onSort={setSort}
                      title="The OI/price conjunction. Filled = fresh money taking a side; hollow = old money leaving. Sorts by how emphatic the move was." />
                    <SortHeader label="PCR" sortKey="pcr" active={sort} onSort={setSort}
                      title="Front-expiry put/call OI ratio, recomputed from live contract OI." />
                    <SortHeader label="MWPL" sortKey="mwpl" active={sort} onSort={setSort} align="right"
                      title="Market-wide position limit utilisation. Past 95% NSE bans fresh F&O in the name." />
                    <SortHeader label="GEX regime" sortKey="gex" active={sort} onSort={setSort} />
                  </>
                )}

                {lens === "volatility" && (
                  <>
                    <SortHeader label="ATM IV" sortKey="atm_iv" active={sort} onSort={setSort}
                      title="Implied volatility, SOLVED by Vanguard from the option's own price. The vendor's iv column stopped for equities on 2026-07-28." />
                    <SortHeader label="IV pctile" sortKey="iv_percentile" active={sort} onSort={setSort} align="right"
                      title="Where this IV sits in the symbol's own trailing 60 sessions. The level alone is not a reading." />
                    <SortHeader label="ΔIV" sortKey="d_atm_iv" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="IVS" sortKey="ivs" active={sort} onSort={setSort} align="right"
                      title="Cremers-Weinbaum call-minus-put IV near the money — the informed-flow quantity M2 is built on." />
                    <SortHeader label="25Δ skew" sortKey="skew" active={sort} onSort={setSort} align="right"
                      title="Risk reversal. Usually absent: the collected chain does not reach the wings, and substituting the nearest strike just measures IVS again." />
                    <SortHeader label="strikes" sortKey="iv_strikes" active={sort} onSort={setSort} align="right"
                      title="How many strikes the chain carried — the breadth behind every number in this lens." />
                  </>
                )}

                {lens === "performance" && (
                  <>
                    <SortHeader label="close" sortKey="close" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="1d" sortKey="d_price" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="5d" sortKey="ret_5d" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="20d" sortKey="ret_20d" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="60d" sortKey="ret_60d" active={sort} onSort={setSort} align="right" />
                    <SortHeader label="RVOL" sortKey="rvol" active={sort} onSort={setSort} align="right" />
                    <th className="py-2 pr-3 uppercase tracking-[0.14em]">buildup</th>
                  </>
                )}

                <SortHeader label="conviction" sortKey="conviction" active={sort} onSort={setSort} />
                <th className="py-2 pr-3 uppercase tracking-[0.14em]">session</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => {
                const selected = selectedSymbol === r.symbol;
                return (
                  <tr
                    key={r.symbol}
                    onClick={() => onPickSymbol?.(selected ? null : r.symbol)}
                    className={
                      "cursor-pointer border-t border-bg-border/50 transition-colors " +
                      (selected ? "bg-accent-blue/10" : "hover:bg-bg-hover/30")
                    }
                  >
                    <td className="py-1.5 pr-3">
                      <div className="font-mono text-[12px] text-text-primary">{r.symbol}</div>
                      <div className="text-[10px] text-text-muted">{r.sector20 ?? "—"}</div>
                    </td>
                    <td className="py-1.5 pr-3">
                      <LegChain legs={legMap(r)} firstFailed={r.survived_filter ? null : r.first_failed_leg} />
                    </td>

                    {lens === "decision" && (
                      <>
                        <td className="py-1.5 pr-3">
                          <ValueAge
                            value={r.flow_score}
                            age={r.flow_age_sessions}
                            maxAge={thresholds.flow_max_age_sessions}
                            unit="s"
                            whyMissing="M2 has never written a flow score for this symbol"
                          />
                        </td>
                        <td className="py-1.5 pr-3">
                          <Ingredients n={r.flow_n_ingredients} min={thresholds.flow_min_ingredients} />
                        </td>
                        <td className="py-1.5 pr-3">
                          <ValueAge
                            value={r.rs_z20}
                            age={r.rs_age_sessions}
                            maxAge={thresholds.rs_max_age_sessions}
                            unit="s"
                            whyMissing="M4 has no sector RS for this symbol's sector20"
                          />
                        </td>
                        <td className="py-1.5 pr-3">
                          <GexScale
                            percentile={num(r.gex_percentile)}
                            regime={r.regime}
                            ageBars={r.regime_age_bars}
                            maxAgeBars={thresholds.regime_max_age_bars}
                          />
                        </td>
                        <td className="py-1.5 pr-3">
                          <div className="flex items-center gap-1.5">
                            <TimingChip state={r.timing_state} />
                            <ScoreBar value={num(r.timing_score)} threshold={thresholds.timing_min_score}
                                      color="green" width={40} />
                          </div>
                        </td>
                        <td className="py-1.5 pr-3 text-right font-mono text-[11px] text-text-secondary">
                          {num(r.rvol) == null
                            ? <Unmeasured why="RVOL needs 5 prior same-time-of-day sessions" short />
                            : (num(r.rvol) as number).toFixed(2)}
                        </td>
                        <td className="py-1.5 pr-3">
                          <ValueAreaGauge position={num(r.va_position)} />
                        </td>
                      </>
                    )}

                    {lens === "positioning" && (
                      <>
                        <td className="py-1.5 pr-3">
                          <OiCell total={num(r.total_oi)} dPct={num(r.d_oi_pct)} source={r.oi_source} />
                        </td>
                        <td className="py-1.5 pr-3 text-right">
                          <PerfCell value={num(r.d_oi_pct)} scale={20} width={38} />
                        </td>
                        <td className="py-1.5 pr-3">
                          <OiStateBadge state={r.oi_state} dOiPct={num(r.d_oi_pct)}
                                        dPricePct={num(r.d_price_pct)} />
                        </td>
                        <td className="py-1.5 pr-3">
                          <PcrCell pcr={num(r.oi_pcr)} dPcr={num(r.d_oi_pcr)} />
                        </td>
                        <td className="py-1.5 pr-3 text-right">
                          <MwplCell pct={num(r.mwpl_pct)} />
                        </td>
                        <td className="py-1.5 pr-3">
                          <GexScale
                            percentile={num(r.gex_percentile)}
                            regime={r.regime}
                            ageBars={r.regime_age_bars}
                            maxAgeBars={thresholds.regime_max_age_bars}
                          />
                        </td>
                      </>
                    )}

                    {lens === "volatility" && (
                      <>
                        <td className="py-1.5 pr-3">
                          <IvCell iv={num(r.atm_iv)} percentile={num(r.iv_percentile)} />
                        </td>
                        <td className="py-1.5 pr-3 text-right font-mono text-[11px] text-text-secondary">
                          {num(r.iv_percentile) == null ? "—" : ((num(r.iv_percentile) as number) * 100).toFixed(0)}
                        </td>
                        <td className="py-1.5 pr-3 text-right">
                          <PerfCell value={num(r.d_atm_iv) == null ? null : (num(r.d_atm_iv) as number) * 100}
                                    scale={3} digits={2} />
                        </td>
                        <td className="py-1.5 pr-3 text-right"><IvsCell ivs={num(r.ivs)} /></td>
                        <td className="py-1.5 pr-3 text-right">
                          <SkewCell skew={num(r.skew_25d)} reason={r.skew_reason} />
                        </td>
                        <td className="py-1.5 pr-3 text-right font-mono text-[11px] text-text-muted">
                          {num(r.iv_n_strikes) ?? "—"}
                        </td>
                      </>
                    )}

                    {lens === "performance" && (
                      <>
                        <td className="py-1.5 pr-3 text-right font-mono text-[11px] text-text-primary">
                          {num(r.close) == null
                            ? <Unmeasured why="no settled close for this session yet" short />
                            : (num(r.close) as number).toFixed(2)}
                        </td>
                        <td className="py-1.5 pr-3 text-right"><PerfCell value={num(r.d_price_pct)} scale={5} /></td>
                        <td className="py-1.5 pr-3 text-right"><PerfCell value={num(r.ret_5d)} scale={10} /></td>
                        <td className="py-1.5 pr-3 text-right"><PerfCell value={num(r.ret_20d)} scale={20} /></td>
                        <td className="py-1.5 pr-3 text-right"><PerfCell value={num(r.ret_60d)} scale={40} /></td>
                        <td className="py-1.5 pr-3 text-right font-mono text-[11px] text-text-secondary">
                          {num(r.rvol) == null ? "—" : (num(r.rvol) as number).toFixed(2)}
                        </td>
                        <td className="py-1.5 pr-3">
                          <OiStateBadge state={r.oi_state} dOiPct={num(r.d_oi_pct)}
                                        dPricePct={num(r.d_price_pct)} />
                        </td>
                      </>
                    )}

                    <td className="py-1.5 pr-3">
                      <ScoreBar value={num(r.conviction)} threshold={thresholds.conviction_min} color="violet" />
                    </td>
                    <td className="py-1.5 pr-3">
                      <Spark values={r.conviction_track} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!filtered.length && (
          <p className="py-6 text-center text-sm text-text-secondary">
            No symbol matches this filter.
            {(diedAt || oiState) && (
              <button type="button" onClick={() => { setDiedAt(""); setOiState(""); }}
                      className="ml-2 inline-flex items-center gap-1 text-accent-blue">
                <X size={11} /> clear
              </button>
            )}
          </p>
        )}
      </Section>

      {!!market?.by_sector?.length && (
        <Section
          title="By sector20"
          icon={<Layers size={16} />}
          description="M4's correlation-reduced sector tier. Average conviction is over every symbol in the bucket, survivors and casualties alike."
        >
          <div className="grid gap-1.5 md:grid-cols-2 xl:grid-cols-3">
            {market.by_sector.map((s: any) => (
              <button
                key={s.sector20}
                type="button"
                onClick={() => setSector(sector === s.sector20 ? "" : s.sector20)}
                className={
                  "flex items-center justify-between gap-3 rounded-xl border px-3 py-2 text-left transition-colors " +
                  (sector === s.sector20
                    ? "border-accent-blue/45 bg-accent-blue/8"
                    : "border-bg-border bg-bg-secondary/25 hover:bg-bg-hover/25")
                }
              >
                <div className="min-w-0">
                  <div className="truncate text-xs text-text-primary">{s.sector20}</div>
                  <div className="text-[10px] text-text-muted">
                    {s.n} names · {s.igniting ?? 0} igniting
                    {s.survivors ? ` · ${s.survivors} survived` : ""}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <ScoreBar value={num(s.avg_conviction)} color="violet" width={44} />
                  <div className="mt-0.5 font-mono text-[10px] text-text-muted"
                       title="mean rs_z20 across this bucket">
                    RS {num(s.avg_rs_z20) == null ? "—" : (num(s.avg_rs_z20) as number).toFixed(2)}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function legMap(row: any): Record<string, boolean | null> {
  return {
    flow_present: row.leg_flow_present,
    flow_fresh: row.leg_flow_fresh,
    flow_strength: row.leg_flow_strength,
    sector_rs: row.leg_sector_rs,
    regime: row.leg_regime,
    timing: row.leg_timing,
  };
}

function Ingredients({ n, min }: { n?: number | null; min?: number | null }) {
  const count = num(n);
  if (count == null) {
    return (
      <span className="cursor-help text-[10px] text-text-muted"
            title="This row predates migration 006 and the ingredient count was not retained. Treated as unknown, not as adequate.">
        ?
      </span>
    );
  }
  const weak = min != null && count < min;
  return (
    <span
      className="inline-flex items-center gap-[2px]"
      title={
        `${count} of 5 ingredients contributed` +
        (weak ? ` — below the ${min} minimum, so this score is rejected as uncorroborated` : "")
      }
    >
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className={
            "h-2.5 w-[3px] rounded-sm " +
            (i < count ? (weak ? "bg-accent-amber" : "bg-accent-blue/70") : "bg-bg-border")
          }
        />
      ))}
    </span>
  );
}

function FilterChip({
  active,
  onClick,
  label,
  tone = "muted",
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  tone?: "muted" | "red" | "green";
}) {
  const base =
    tone === "red"
      ? "border-accent-red/30 text-accent-red"
      : tone === "green"
        ? "border-accent-green/30 text-accent-green"
        : "border-bg-border text-text-muted";
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-md border px-1.5 py-0.5 font-mono text-[10px] transition-colors " +
        (active ? "border-accent-blue/60 bg-accent-blue/12 text-text-primary" : `${base} hover:bg-bg-hover/30`)
      }
    >
      {label}
    </button>
  );
}
