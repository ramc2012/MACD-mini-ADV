export type Candle = {
  symbol: string;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closed: boolean;
};

export type ChartMarker = { timestamp: number; price: number; label: string; tone?: "entry" | "stop" | "signal" };

export type Tick = { symbol: string; ltp: number; volume: number; prev_close?: number; change?: number; change_pct?: number; timestamp: string };
export type SpotWatchRow = { symbol: string; tick?: Tick; indicator?: Indicator };
export type OptionWatchRow = SpotWatchRow & {
  sector?: string; underlying: string; spot_symbol: string; option_type: "CE" | "PE"; strike: number; expiry: string;
  selection_price: number; liquidity_volume: number; oi: number; retained: boolean; lot_size: number;
  moneyness: "ITM" | "ATM" | "OTM"; analysis_only: boolean;
  // Modelled, not fed: implied vol solved from the premium, then gamma -> GEX.
  iv?: number | null; gamma?: number | null; gex?: number | null;
};
export type Indicator = {
  symbol: string; timestamp: number; macd: number; signal: number; histogram: number;
  bb_middle?: number; bb_upper?: number; bb_lower?: number; bb_width?: number; kama?: number; kama_rsi?: number; kama_roc?: number;
};
export type Signal = Indicator & { signal_id: string; side: "BUY" | "SELL"; kind: string; price: number; evaluated_candle_timestamp?: number };
export type Order = {
  order_id: string; symbol: string; side: string; quantity: number; lots: number; lot_size: number; order_type: string;
  status: string; fill_price?: number; signal_id?: string; created_at: string;
};
export type Trade = {
  trade_id: string; symbol: string; side: string; quantity: number; lots: number; lot_size: number; price: number; timestamp: string;
  fees?: number;
};
export type Position = {
  symbol: string; position_id?: string; quantity: number; lots: number; lot_size: number; average_price: number; last_price: number;
  unrealized_pnl: number; return_pct?: number; opened_at: string;
  // Excursion since open (MFE/MAE): running extremes of the instantaneous return against the
  // average cost at the time of each mark, so a pyramid does not rewrite the path. Optional so a
  // frame from an older backend still type-checks; the columns render "—" when absent.
  max_price?: number | null; min_price?: number | null; max_return_pct?: number | null; min_return_pct?: number | null;
  peak_price?: number; hard_stop?: number; trailing_stop?: number | null; entry_anchor?: number; entry_stage?: number; exit_stage?: number; entry_fees?: number;
};
// One closing slice of a paper position. A staged exit produces one record per SELL fill sharing
// position_id with partial=true until the last lot goes. Field names are the backend's.
export type ClosedPosition = {
  closed_id: string; position_id: string; symbol: string; lane: string; side: "LONG" | "SHORT";
  quantity: number; lots: number; lot_size: number;
  entry_time: string; entry_price: number; exit_time: string; exit_price: number;
  gross_pnl: number; fees: number; realized_pnl: number; return_pct: number;
  max_price: number | null; min_price: number | null; max_return_pct: number | null; min_return_pct: number | null;
  exit_reason: string | null; partial: boolean; remaining_quantity: number; exit_trade_id: string;
  // ISO datetime; the desk hides the record once this passes (08:00 IST after the exit).
  visible_until: string;
};
// The four excursion keys on their own, for columns shared by open and closed rows.
export type Excursion = Pick<Position, "max_price" | "min_price" | "max_return_pct" | "min_return_pct">;
export type Portfolio = {
  equity: number; cash: number; market_value: number; realized_pnl: number; unrealized_pnl: number; positions: Position[];
  lane?: string;
  // Positions closed since the last 08:00 IST boundary; pruned server-side.
  closed_positions?: ClosedPosition[];
};
export type ClosedBook = { closed_positions?: ClosedPosition[] };
export type Snapshot = {
  broker: { name: string; status: string; symbols: string[]; error?: string };
  config: { feed_mode: string; timeframe_seconds: number; fast: number; slow: number; signal: number; bb: number[]; kama: number[]; kama_rsi?: number[]; kama_roc?: number[]; entry_confirmations?: { macd_zero_cross_up: boolean; kama: boolean; kama_rsi: boolean; kama_roc: boolean }; entry_volume_ratio: number; signal_mode: string; auto_trade: boolean; market_holidays?: string[] };
  // Only symbols absent from spot_watchlist and option_watchlist.
  watchlist: { symbol: string; tick?: Tick }[];
  spot_watchlist: SpotWatchRow[];
  option_watchlist: OptionWatchRow[];
  contract_selection: { basis: string; errors: Record<string, string> };
  candles: Record<string, Candle[]>;
  history_errors: Record<string, string>;
  strategy: { indicator_history: Record<string, Indicator[]>; signals: Signal[] };
  execution: { mode: string; live_orders_unlocked: boolean; orders: Order[]; trades: Trade[]; portfolio: Portfolio; closed_positions?: ClosedPosition[]; risk: { hard_stop_pct: number; trailing_stop_pct: number; max_positions?: number; min_cash_reserve?: number; slippage_bps?: number; target_position_notional?: number; max_trade_lots?: number; entry_filter?: string } };
  // Optional so a frame from a backend without the blast lane still type-checks.
  blast?: BlastSnapshot;
};

export type ResearchTrade = {
  trade_id: string; run_id: string; symbol: string; underlying: string; option_type: string; fold: string;
  signal_time: string; entry_time: string; exit_time: string; entry_price: number; exit_price: number;
  quantity: number; lots: number; lot_size: number; pnl: number; return_pct: number; exit_reason: string;
};

export type ResearchSummary = {
  run_id: string; method: string; contracts_selected: number; contracts_tested: number; trades: number;
  closed_trades: number; wins: number; win_rate_pct: number; net_pnl: number; average_return_pct: number;
  max_drawdown: number; exit_reasons: Record<string, number>;
  open_positions: number; open_unrealized_pnl: number;
};

export type EquityPoint = { timestamp: string; equity: number; drawdown: number };

export type ResearchOpenPosition = {
  position_id: string; run_id: string; status: "OPEN"; symbol: string; underlying: string; option_type: string;
  expiry: string; fold: string; signal_time: string; entry_time: string; last_time: string; entry_price: number;
  last_price: number; quantity: number; lots: number; lot_size: number; peak_price: number; hard_stop: number;
  trailing_stop?: number; unrealized_pnl: number; unrealized_return_pct: number;
};

export type StreamEvent = { seq: number; type: string; data: unknown };

export type HealthInfo = {
  history_error_details?: Record<string, string>; warmup?: { required: number; with_indicators: number };
  session_open?: boolean; feed_alive?: boolean; observed_at?: string; feed_recoveries?: number; preopen_refreshes?: number;
  status: string; error?: string; uptime_seconds: number; feed_connects: number; ticks_total: number;
  tick_rate_per_second: number; last_tick_age_seconds?: number; candles_closed: number; signals_emitted: number;
  symbols: number; history_errors: number; stream_clients: number; day_baseline_equity?: number | null;
  chain?: { snapshots: number; last_at: string | null; error: string | null; windows: number; alerts_today: number;
            history_days: number; composite: Record<string, number | null>; whale_error: string | null };
};

export type SignalEvaluation = {
  symbol: string; timestamp: number; close: number; macd: number; previous_macd: number;
  cross: boolean; kama_ok: boolean; kama_rsi?: number; kama_roc?: number; rsi_ok: boolean; roc_ok: boolean; bb_ok: boolean;
  kama_required: boolean; rsi_required: boolean; roc_required: boolean;
  volume_ratio: number; passed: number; required_count: number; fired: boolean;
};

export type Diagnostics = {
  evaluated: number;
  condition_totals: { cross: number; kama_ok: number; rsi_ok: number; roc_ok: number; fired: number };
  enabled_conditions: { macd_zero_cross_up: boolean; kama: boolean; kama_rsi: boolean; kama_roc: boolean };
  required_count: number;
  rows: SignalEvaluation[];
};

export type LiveEquityPoint = { timestamp: string; equity: number; cash: number; realized_pnl: number; unrealized_pnl: number };

export type RRGPoint = { x: number; y: number };
export type RRGSymbolRow = { symbol: string; sector: string; tail: RRGPoint[]; x: number; y: number; quadrant: string };
export type RRGSectorRow = { sector: string; members: number; tail: RRGPoint[]; x: number; y: number; quadrant: string };
export type RRGData = {
  benchmark: string; timeframe_seconds: number; window: number; tail: number; generated_at: string;
  evaluated: number; unmapped: string[]; sectors: RRGSectorRow[]; symbols: RRGSymbolRow[];
};

export type RatioPoint = { time: number; value: number };
export type PremiumRatioSeries = {
  symbol: string; side: "CE" | "PE"; moneyness: "ITM" | "ATM" | "OTM";
  strike: number; label: string; color: string; points: RatioPoint[];
};
export type OptionRatioSeries = {
  key: string; side: "CE" | "PE"; label: string; numerator: string; denominator: string;
  points: RatioPoint[]; ema: RatioPoint[]; ema_period: number;
};
export type RatioHistory = {
  spot_symbol: string; underlying: string; expiry: string; timeframe_seconds: number;
  contracts: PremiumRatioSeries[]; ratios: OptionRatioSeries[]; bars: Record<string, number>;
  download_errors: Record<string, string>;
};

export type ProfileRow = {
  symbol: string; day: string; open: number | null; high: number | null; low: number | null;
  close: number | null; poc: number | null; vah: number | null; val: number | null;
  ib_high: number | null; ib_low: number | null; volume: number; buy_volume: number;
  sell_volume: number; cumulative_delta: number; imbalance: number | null; trades: number;
  day_type: string | null; value_migration: string | null; levels: number; source: string;
  single_prints?: number[];
};
export type PeriodRow = ProfileRow & {
  period: string; period_start: string; period_end: string; sessions: number; naked_pocs: number[];
};
export type AuctionContext = {
  symbol: string; as_of: string; price: number | null; sessions_available: number;
  value_migration: string; prior_day: ProfileRow | null; week: PeriodRow | null;
  month: PeriodRow | null; naked_pocs: number[]; levels: Record<string, number>;
  location: Record<string, string>; alignment: string; latest_session: ProfileRow | null;
  regime: { regime_id: string; summary: string; start: string; session_end: string;
            nifty_lot: number; nifty_futures_tick: number; weekly_expiry: string | null };
};
export type Rate = [number, number];
export type BaseRateSummary = {
  sessions: number; sessions_with_ib: number; ib_broken: Rate; break_up_only: Rate;
  break_down_only: Rate; break_both: Rate; first_break_in_C: Rate; first_break_in_D: Rate;
  median_extension_ratio: number | null; median_range_ib_ratio: number | null;
  opened_outside_value: Rate; returned_to_value: Rate; rule80_triggered: Rate;
  rule80_completed: Rate; gapped: Rate; gap_filled: Rate;
  extension_over_25pct: Rate; extension_over_50pct: Rate;
  extension_over_100pct: Rate; extension_over_200pct: Rate;
};
export type BaseRateGroup = { key: string; title: string; start?: string; sessions: number; summary: BaseRateSummary };
export type BaseRates = {
  symbol: string; span: [string, string] | null; crosses: string[]; groups: BaseRateGroup[];
  today: { day: string; regime_id: string; regime_sessions: number;
           measurement: Record<string, unknown>;
           comparisons: { label: string; today: boolean; base_rate: number; sample: number }[] } | null;
};
export type FlowMinute = {
  time: number; close: number | null; volume: number; delta: number; cvd: number;
  ofi: number; cumulative_ofi: number; ofi_events: number; confidence: number | null;
};
export type AuctionFlow = {
  symbol: string; day: string; minutes: number; series: FlowMinute[];
  cumulative_delta: number; cumulative_ofi: number; diverged: boolean;
  mean_confidence: number | null; available_days: string[];
};
export type SetupSummary = {
  setup: string; name: string; kind: string; measurable: boolean; claimed_edge: string;
  sessions: number; context: Rate; triggered: Rate; outcome: Rate;
};
export type SetupJournal = {
  symbol: string; regime_id: string | null;
  catalogue: { setup: string; name: string; kind: string; needs_tick_features: boolean }[];
  summary: SetupSummary[];
  recent: { day: string; setup: string; context: number | null; triggered: number | null;
            outcome: number | null; direction: string | null; detail: string }[];
};
export type WhaleEvent = { symbol: string; ts_ms: number; kind: string; side: number; quantity: number | null; price: number | null; score: number; evidence: string | null };
export type WhaleStrike = {
  strike: number; option_type: string; symbol: string; expiry: string;
  oi: number; prior_oi: number; d_oi: number; d_volume: number; ltp: number; d_ltp: number;
  iv: number | null; delta: number; delta_source: "model" | "intrinsic" | "none";
  flow_sign: number; flow_source: "prints" | "premium" | "none";
  dn: number; contribution: number; bucket: number; unusual: string[];
};
export type WhaleStructure = { kind: string; direction: number; legs: [number, string, number, number, number][]; dn: number };
export type WhaleWindow = {
  underlying: string; status: string; source: string; as_of: number; then: number | null; day: string; slot: number;
  spot: number | null; fut: number | null; vix: number | null; expiry: string | null; lot: number;
  strikes: WhaleStrike[]; unusual: WhaleStrike[]; structures: WhaleStructure[];
  walls: Record<string, { strike: number; oi: number }[]>;
  migration: Record<string, { from: number | null; to: number | null }>;
  pcr_oi: number | null; pcr_oi_then: number | null; pcr_jump: number | null; pcr_jumped: boolean; pcr_vol_window: number | null;
  net_option_delta: { units: number; dn: number; sign: number; signed_legs: number; unsigned_legs: number };
  futures: { symbol: string | null; delta_units: number; ofi: number | null; dn: number | null; oi: number | null; d_oi: number | null;
             ltp?: number; ofi_normalised?: number | null; volume?: number; trades?: number } | null;
  divergence: { status: string; divergence: boolean; ratio: number | null; fut_sign?: number | null; option_sign?: number; fresh_futures?: boolean };
  aggression_net: number;
  scores: { a: number | null; b: number | null; c: number | null; d: number | null };
  z: { a: number | null; b: number | null; c: number | null; d: number | null };
  composite: number | null; composite_decayed: number | null;
  history: { days: number; required: number; status: "ok" | "insufficient"; regime_breaks?: string[] };
  alert_id: number | null;
};
export type WhaleOutcome = { spot: number; move_pts: number; move_pct: number; agreed: boolean } | null;
export type WhaleAlert = {
  id: number; underlying: string; ts: number; day: string; composite: number; direction: number;
  spot: number | null; fut: number | null; evidence: Record<string, unknown>; sent: number;
  next_15: WhaleOutcome; next_30: WhaleOutcome; next_60: WhaleOutcome;
};
export type WhaleEod = {
  day: string; underlying: string; close_ts: number; spot: number | null; fut: number | null;
  call_oi: number; put_oi: number; pcr_oi: number | null; prior_pcr_oi: number | null; d_call_oi: number; d_put_oi: number;
  fut_oi: number | null; fut_pdoi: number | null; fut_avg_trade: number | null; fut_avg_trade_pct: number | null;
  top_oi: [number, string, number, number][]; top_d_oi: [number, string, number, number][];
  walls: Record<string, number[]>; score: number | null; z: number | null; history_days: number;
};
export type WhaleView = {
  day: string; events: WhaleEvent[];
  aggression: Record<string, { buy: number; sell: number; net: number }>;
  chains: Record<string, { snapshots: number; as_of?: number; spot?: number | null; pcr_oi?: number | null; pcr_volume?: number | null;
    strikes: { strike: number; type: string; oi: number; d_oi: number; d_volume: number }[];
    walls?: { strike: number; type: string; oi: number }[] }>;
  windows: Record<string, WhaleWindow>;
  eod: Record<string, WhaleEod>;
  alerts: WhaleAlert[];
  history: { chain_days: number; required: number };
};

// Path excursion since open (MFE / MAE), relative to average_price — the same
// base as the Portfolio page's Change % column. Null when the backend could not
// observe the path: positions rebuilt from the trade log at boot carry no ticks.
// Positions closed since the last 08:00 IST, pruned server-side and filtered
// again on the client. Optional so a backend that predates the record still
// type-checks; the Ledger then derives the book from the trade log instead.
// Terminal chart preferences, persisted by App under macd.chartLayers / macd.chartPanes.
export type ChartLayers = { volume: boolean; legend: boolean; priorDay: boolean; week: boolean; nakedPocs: boolean; ib: boolean; sessions: boolean };
export type PaneCollapse = { rsi: boolean; roc: boolean };
/* ---- Profile / Flow page: live OFI, tape speed, session badges, replay ----
   The per-BAR additions (confidence, marked prints) live in FootprintChart's
   own types beside the bars they qualify; these are the page-level shapes
   OrderFlowWorkspace, VolumeProfilePane and the Auction sub-page share. */
export type OfiLive = {
  symbol: string; cumulative: number; events: number; ofi_60s: number; ofi_300s: number;
  // Null until the trailing depth window has enough samples to normalise
  // against. The raw figure is in quantity units and is not comparable
  // between contracts, which is what the normalised pair is for.
  normalised_60s: number | null; normalised_300s: number | null; depth_scale: number | null;
  since: number | null;
  series?: { t: number; ofi: number; cum: number; events: number }[];
};
export type TapeSpeed = {
  window_seconds: number; updates_per_s: number; qty_per_s: number;
  // Withheld (null) until enough windows have closed today to rank against.
  updates_pct: number | null; qty_pct: number | null; samples: number;
};
export type MarkedPrint = {
  t: number; p: number; s: number; side: number;
  kind: "freeze" | "large"; lots?: number; ratio?: number;
};
export type SessionQuality = {
  grade: "high" | "fair" | "low" | null; prints: number;
  quote_share: number | null; depth_tick_share: number | null;
  unclassified_share: number | null; mean_confidence: number | null;
};
export type SessionInfo = {
  symbol: string; day: string;
  open_type: string; open_observed: boolean; open_location: string | null;
  // Two taxonomies that disagree by construction: day_type_ib reads range
  // extension past the initial balance, day_type_va reads value-area coverage
  // and is the one the stored base rates are keyed on. Both are shown, named.
  day_type_ib: string; day_type_va: string; day_type_bracket: string | null;
  day_type_by_bracket: { bracket: number; letter: string; day_type: string }[];
  partial_capture?: boolean;
  ib_complete: boolean; ib_high: number | null; ib_low: number | null;
  extension: { up: number | null; down: number | null; ratio: number | null };
  value_relationship: string;
  prior_day: { poc: number | null; vah: number | null; val: number | null; high: number | null; low: number | null; close?: number | null };
  prior_day_date: string | null;
  vix: number | null; vix_implied_range: number | null; vix_reference_price: number | null;
  expiry_day: boolean; expiring: boolean; regime_id: string;
  poor_high: boolean | null; poor_low: boolean | null; tail_high: number; tail_low: number;
  quality: SessionQuality;
};
export type CompositeProfile = {
  symbol: string; days: number; requested_days: number; from: string | null; to: string | null;
  poc: number | null; vah: number | null; val: number | null; high: number | null; low: number | null;
  levels: { price: number; volume: number }[];
};
export type MarketContext = {
  // Flat "pd_vah" / "week_poc" / "month_low" name -> price map, exactly as
  // MarketReference.levels() publishes it.
  levels: Record<string, number>;
  naked_pocs: number[];
  prior_day: Record<string, number | string | null> | null;
};
export type ReplayMeta = {
  day: string; at: number; session_start: number; session_end: number;
  ticks: number; position: number;
};
export type VaByBracket = { bracket: number; letter: string; poc: number | null; vah: number | null; val: number | null };

/* ---- Blast lane --------------------------------------------------------
   A third paper book. Field names are the backend's (blast_engine.py); every
   stream frame for it is namespaced blast_*, so none of this can collide with
   the MACD lane's orders, trades or portfolio. */
export type BlastSettings = {
  enabled: boolean; auto_trade: boolean; initial_capital: number; max_positions: number; target_notional: number;
  // Percent of spot, fraction of contracts, percent below the recent high.
  max_premium_pct: number; min_breadth: number; min_off_high_pct: number;
  // Fractions: 0.5 is a 50% stop.
  hard_stop_pct: number; trail_activation_pct: number; trail_pct: number;
};
export type BlastHealth = {
  enabled: boolean; auto_trade: boolean; evaluated_since_start: number; taken_since_start: number; watching: number;
  open_positions: number; symbols_with_history: number; last_candidate_at: string | null; broker_access: string; error: string | null;
  bar_seconds?: number; warmed_symbols?: number; last_warm_at?: string | null; late_bars_skipped?: number;
};
export type BlastVerdictSummary = { reason: string; count: number; resolved: number; mean_mfe_pct: number | null; mean_mae_pct: number | null };
export type BlastJournalSummary = { day: string | null; evaluated: number; taken: number; verdicts: BlastVerdictSummary[] };
export type BlastCandidate = {
  id: string; at: string; day: string; symbol: string; bar_timestamp: number | null;
  spot_symbol: string | null; option_type: string; strike: number | null; expiry: string | null;
  premium: number; spot: number | null; premium_pct: number | null; breadth: number | null;
  off_high_pct: number | null; high_ref: number | null; lookback_bars: number;
  macd: number; signal: number; histogram: number;
  taken: number; reason: string; order_id: string | null; watch_until: string | null;
  // Written back by the forward watcher; absent on a live frame.
  mfe_pct?: number | null; mae_pct?: number | null; resolved?: boolean;
};
export type BlastSnapshot = {
  lane: "blast"; enabled: boolean; auto_trade: boolean; settings: BlastSettings; health: BlastHealth;
  screen: { entry: string; max_premium_pct_of_spot: number; min_side_breadth: number; min_off_recent_high_pct: number;
            recent_high_lookback_bars: number; max_volume_share_pct: number;
            journal_horizon_hours: number; bar_seconds?: number };
  risk: { hard_stop_pct: number; trailing_activation_profit_pct: number; trailing_stop_pct: number;
          target_position_notional: number; max_positions: number; last_exit_reasons: Record<string, string> };
  counters: { evaluated: number; taken: number; verdicts: Record<string, number>; watching: number; symbols_with_history: number };
  portfolio: Portfolio; closed_positions: ClosedPosition[]; journal_summary: BlastJournalSummary; error: string | null;
};
export type BlastJournalResponse = { rows: BlastCandidate[]; summary: BlastJournalSummary; days: string[] };
export type BlastBook = {
  orders: Order[]; trades: Trade[]; signals: Signal[]; equity: LiveEquityPoint[];
  portfolio: Portfolio; closed_positions: ClosedPosition[];
};
