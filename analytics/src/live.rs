//! Live per-symbol analytics built from the tick bus.
//!
//! Each symbol is owned by exactly one worker shard, so its ticks are applied
//! in arrival order without locks shared across symbols, and shards run in
//! parallel on separate cores. A symbol builds its own one-minute bars from
//! exchange time, and updates MACD and realized volatility when a bar closes.
//! Indicator state is seeded from the engine's stored minute bars, so values
//! are meaningful from the first live bar instead of after a warm-up.

use crate::{MacdPeriods, TRADING_DAYS_PER_YEAR, TRADING_SECONDS_PER_DAY};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

const IST_OFFSET_SECONDS: i64 = 19_800;
const SESSION_OPEN_MINUTE: i64 = 9 * 60 + 15;
const SESSION_CLOSE_MINUTE: i64 = 15 * 60 + 30;
const MINUTE_MS: i64 = 60_000;
/// Adjacent one-minute returns kept for realized volatility: two hours.
const RETURN_WINDOW: usize = 120;
/// Bars this service closed recently, re-applied over a seed that predates them.
const RECENT_BARS: usize = 16;
/// A print received this long after its exchange time is not a new trade: on
/// connect Fyers republishes each contract's last trade (Friday 15:29 on a
/// Sunday), and quote-only updates carry the last trade's old time. Such a
/// print moves the displayed price but builds no bar and is not stored.
pub const STALE_PRINT_MS: i64 = 120_000;

/// What one tick did.
#[derive(Debug)]
pub struct TickOutcome {
    pub closed: Option<ClosedBar>,
    /// A fresh session trade: it entered a bar and belongs in the tick store.
    pub recorded: bool,
}

/// The gateway's versioned tick contract (`busTick` in the Go gateway).
#[derive(Clone, Debug, Deserialize)]
pub struct BusTick {
    pub v: u32,
    pub seq: i64,
    pub symbol: String,
    pub ltp: f64,
    #[serde(default)]
    pub volume: i64,
    pub exchange_ts_ms: i64,
    #[serde(default)]
    pub gateway_ts_ms: i64,
}

/// NSE regular session, Monday to Friday, judged on exchange time. Exchange
/// holidays are not known here; the engine publishes no session ticks then.
pub fn in_session(ts_ms: i64) -> bool {
    let local = ts_ms.div_euclid(1000) + IST_OFFSET_SECONDS;
    let minute = local.rem_euclid(86_400) / 60;
    let weekday = (local.div_euclid(86_400) + 4).rem_euclid(7); // 1970-01-01 was a Thursday; 0 = Sunday
    weekday != 0 && weekday != 6 && (SESSION_OPEN_MINUTE..SESSION_CLOSE_MINUTE).contains(&minute)
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Bar {
    pub start_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    /// Traded in the bar, from the day's cumulative volume. Zero for indices.
    pub volume: i64,
    pub ticks: u32,
    #[serde(skip)]
    first_cumulative_volume: i64,
}

impl Bar {
    fn open_at(minute: i64, tick: &BusTick) -> Self {
        Self {
            start_ms: minute * MINUTE_MS,
            open: tick.ltp,
            high: tick.ltp,
            low: tick.ltp,
            close: tick.ltp,
            volume: 0,
            ticks: 1,
            first_cumulative_volume: tick.volume,
        }
    }

    fn update(&mut self, tick: &BusTick) {
        self.high = self.high.max(tick.ltp);
        self.low = self.low.min(tick.ltp);
        self.close = tick.ltp;
        self.volume = (tick.volume - self.first_cumulative_volume).max(0);
        self.ticks += 1;
    }
}

#[derive(Clone, Debug, Default)]
struct Macd {
    fast: Option<f64>,
    slow: Option<f64>,
    signal: Option<f64>,
}

impl Macd {
    /// The engine's rule (and `analyze`'s): the first close seeds both EMAs
    /// and the first MACD value seeds the signal line.
    fn step(&mut self, close: f64, periods: MacdPeriods) {
        let ema = |previous: Option<f64>, value: f64, period: usize| match previous {
            Some(previous) => previous + (2.0 / (period as f64 + 1.0)) * (value - previous),
            None => value,
        };
        self.fast = Some(ema(self.fast, close, periods.fast));
        self.slow = Some(ema(self.slow, close, periods.slow));
        let macd = self.fast.unwrap() - self.slow.unwrap();
        self.signal = Some(ema(self.signal, macd, periods.signal));
    }

    fn macd(&self) -> Option<f64> {
        Some(self.fast? - self.slow?)
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum Seed {
    Unrequested,
    Pending,
    Seeded { bars: usize },
    Failed { error: String },
}

#[derive(Clone, Debug, Serialize)]
pub struct ClosedBar {
    pub symbol: String,
    pub bar: Bar,
    pub macd: Option<f64>,
    pub signal: Option<f64>,
    pub histogram: Option<f64>,
    pub realized_volatility_pct: Option<f64>,
    pub periods: MacdPeriods,
}

#[derive(Clone, Debug)]
pub struct SymbolState {
    pub symbol: String,
    pub ltp: f64,
    pub exchange_ts_ms: i64,
    pub last_seq: i64,
    pub ticks: u64,
    pub late_ticks: u64,
    pub off_session_ticks: u64,
    pub stale_ticks: u64,
    bar: Option<Bar>,
    macd: Macd,
    pub bars_closed: u64,
    last_closed_minute: Option<i64>,
    last_close: Option<f64>,
    returns: VecDeque<f64>,
    recent: VecDeque<(i64, f64)>,
    pub seed: Seed,
}

impl SymbolState {
    pub fn new(symbol: &str) -> Self {
        Self {
            symbol: symbol.to_owned(),
            ltp: 0.0,
            exchange_ts_ms: 0,
            last_seq: 0,
            ticks: 0,
            late_ticks: 0,
            off_session_ticks: 0,
            stale_ticks: 0,
            bar: None,
            macd: Macd::default(),
            bars_closed: 0,
            last_closed_minute: None,
            last_close: None,
            returns: VecDeque::with_capacity(RETURN_WINDOW),
            recent: VecDeque::with_capacity(RECENT_BARS),
            seed: Seed::Unrequested,
        }
    }

    /// Apply one tick: it may move the price, enter a bar, and close one.
    pub fn on_tick(&mut self, tick: &BusTick, periods: MacdPeriods) -> TickOutcome {
        let skipped = TickOutcome { closed: None, recorded: false };
        self.ticks += 1;
        self.last_seq = self.last_seq.max(tick.seq);
        if !tick.ltp.is_finite() || tick.ltp <= 0.0 {
            return skipped;
        }
        if tick.exchange_ts_ms >= self.exchange_ts_ms {
            self.ltp = tick.ltp;
            self.exchange_ts_ms = tick.exchange_ts_ms;
        }
        if !in_session(tick.exchange_ts_ms) {
            // Pre-open and post-close republishes value a holding but are
            // not trades in any session bar.
            self.off_session_ticks += 1;
            return skipped;
        }
        if tick.gateway_ts_ms > 0 && tick.gateway_ts_ms - tick.exchange_ts_ms > STALE_PRINT_MS {
            self.stale_ticks += 1;
            return skipped;
        }
        let minute = tick.exchange_ts_ms.div_euclid(MINUTE_MS);
        if let Some(bar) = self.bar.as_mut() {
            let open_minute = bar.start_ms / MINUTE_MS;
            if minute == open_minute {
                bar.update(tick);
                return TickOutcome { closed: None, recorded: true };
            }
            if minute < open_minute {
                self.late_ticks += 1;
                return skipped;
            }
        }
        if self.last_closed_minute.is_some_and(|closed| minute <= closed) {
            self.late_ticks += 1; // a print for a bar that has already closed
            return skipped;
        }
        let closed = self.bar.take().map(|bar| self.close(bar, periods));
        self.bar = Some(Bar::open_at(minute, tick));
        TickOutcome { closed, recorded: true }
    }

    /// Close a bar whose minute has passed with no later tick to close it.
    pub fn sweep(&mut self, now_ms: i64, grace_ms: i64, periods: MacdPeriods) -> Option<ClosedBar> {
        let due = self.bar.as_ref().is_some_and(|bar| now_ms >= bar.start_ms + MINUTE_MS + grace_ms);
        if !due {
            return None;
        }
        let bar = self.bar.take()?;
        Some(self.close(bar, periods))
    }

    fn close(&mut self, bar: Bar, periods: MacdPeriods) -> ClosedBar {
        let minute = bar.start_ms / MINUTE_MS;
        self.record_close(minute, bar.close, periods);
        if self.recent.len() == RECENT_BARS {
            self.recent.pop_front();
        }
        self.recent.push_back((minute, bar.close));
        ClosedBar {
            symbol: self.symbol.clone(),
            macd: self.macd.macd(),
            signal: self.macd.signal,
            histogram: self.histogram(),
            realized_volatility_pct: self.realized_volatility_pct(),
            periods,
            bar,
        }
    }

    fn record_close(&mut self, minute: i64, close: f64, periods: MacdPeriods) {
        // Only adjacent bars form a one-minute return; the overnight gap or a
        // missing minute spans more time than the annualization assumes.
        if let (Some(previous_minute), Some(previous_close)) = (self.last_closed_minute, self.last_close) {
            if minute == previous_minute + 1 {
                if self.returns.len() == RETURN_WINDOW {
                    self.returns.pop_front();
                }
                self.returns.push_back(close.ln() - previous_close.ln());
            }
        }
        self.macd.step(close, periods);
        self.bars_closed += 1;
        self.last_closed_minute = Some(minute);
        self.last_close = Some(close);
    }

    /// Rebuild indicators from the engine's stored minute bars (timestamps in
    /// seconds), then re-apply bars this service closed after that history.
    pub fn apply_seed(&mut self, history: &[(i64, f64)], periods: MacdPeriods) {
        let open_minute = self.bar.as_ref().map(|bar| bar.start_ms / MINUTE_MS);
        let recent: Vec<(i64, f64)> = self.recent.iter().copied().collect();
        self.reset_indicators();
        let mut rows: Vec<(i64, f64)> = history
            .iter()
            .map(|(seconds, close)| (seconds.div_euclid(60), *close))
            .filter(|(minute, close)| close.is_finite() && *close > 0.0 && open_minute.map_or(true, |open| *minute < open))
            .collect();
        rows.sort_by_key(|(minute, _)| *minute);
        rows.dedup_by_key(|(minute, _)| *minute);
        let seeded = rows.len();
        for (minute, close) in rows {
            self.record_close(minute, close, periods);
        }
        for (minute, close) in recent {
            if self.last_closed_minute.map_or(true, |last| minute > last) {
                self.record_close(minute, close, periods);
            }
        }
        self.seed = Seed::Seeded { bars: seeded };
    }

    /// Periods changed: indicator state no longer means anything.
    pub fn reset_indicators(&mut self) {
        self.macd = Macd::default();
        self.returns.clear();
        self.bars_closed = 0;
        self.last_closed_minute = None;
        self.last_close = None;
    }

    fn histogram(&self) -> Option<f64> {
        Some(self.macd.macd()? - self.macd.signal?)
    }

    pub fn realized_volatility_pct(&self) -> Option<f64> {
        if self.returns.len() < 2 {
            return None;
        }
        let n = self.returns.len() as f64;
        let mean = self.returns.iter().sum::<f64>() / n;
        let variance = self.returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / (n - 1.0);
        let bars_per_year = TRADING_DAYS_PER_YEAR * TRADING_SECONDS_PER_DAY / 60.0;
        Some(variance.sqrt() * bars_per_year.sqrt() * 100.0)
    }

    pub fn view(&self, periods: MacdPeriods) -> LiveView {
        let macd = self.macd.macd();
        LiveView {
            symbol: self.symbol.clone(),
            ltp: self.ltp,
            exchange_ts_ms: self.exchange_ts_ms,
            ticks: self.ticks,
            late_ticks: self.late_ticks,
            off_session_ticks: self.off_session_ticks,
            stale_ticks: self.stale_ticks,
            bar: self.bar.clone(),
            bars_closed: self.bars_closed,
            ema_fast: self.macd.fast,
            ema_slow: self.macd.slow,
            macd,
            signal: self.macd.signal,
            histogram: self.histogram(),
            realized_volatility_pct: self.realized_volatility_pct(),
            trend: match macd {
                Some(value) if value > 1e-10 => "bullish",
                Some(value) if value < -1e-10 => "bearish",
                Some(_) => "neutral",
                None => "no_data",
            },
            // An EMA seeded from its first close needs a few multiples of its
            // period before it stops reflecting where it started.
            warm: self.bars_closed >= (periods.slow + periods.signal) as u64,
            seed: self.seed.clone(),
            periods,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct LiveView {
    pub symbol: String,
    pub ltp: f64,
    pub exchange_ts_ms: i64,
    pub ticks: u64,
    pub late_ticks: u64,
    pub off_session_ticks: u64,
    pub stale_ticks: u64,
    pub bar: Option<Bar>,
    pub bars_closed: u64,
    pub ema_fast: Option<f64>,
    pub ema_slow: Option<f64>,
    pub macd: Option<f64>,
    pub signal: Option<f64>,
    pub histogram: Option<f64>,
    pub realized_volatility_pct: Option<f64>,
    pub trend: &'static str,
    pub warm: bool,
    pub seed: Seed,
    pub periods: MacdPeriods,
}

/// Stable symbol-to-shard assignment, so a symbol's ticks always reach the
/// same worker and stay in order. FNV-1a's low bits depend only on the low
/// bits of the input bytes -- names like NSE:LOAD0001 all landed on even
/// shards -- so the hash is finished with MurmurHash3's 64-bit mixer.
pub fn shard_for(key: &str, shards: usize) -> usize {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in key.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash ^= hash >> 33;
    hash = hash.wrapping_mul(0xff51_afd7_ed55_8ccd);
    hash ^= hash >> 33;
    hash = hash.wrapping_mul(0xc4ce_b9fe_1a85_ec53);
    hash ^= hash >> 33;
    (hash % shards.max(1) as u64) as usize
}

/// QuestDB line-protocol records, stamped with exchange time.
pub fn tick_line(tick: &BusTick) -> String {
    format!(
        "ticks,symbol={} ltp={},volume={}i,seq={}i,gateway_lag_ms={}i {}\n",
        crate::escape_tag(&tick.symbol),
        tick.ltp,
        tick.volume,
        tick.seq,
        tick.gateway_ts_ms - tick.exchange_ts_ms,
        tick.exchange_ts_ms * 1_000_000,
    )
}

pub fn bar_line(closed: &ClosedBar) -> String {
    let mut line = format!(
        "bars_1m,symbol={} open={},high={},low={},close={},volume={}i,ticks={}i,fast_period={}i,slow_period={}i,signal_period={}i",
        crate::escape_tag(&closed.symbol),
        closed.bar.open,
        closed.bar.high,
        closed.bar.low,
        closed.bar.close,
        closed.bar.volume,
        closed.bar.ticks,
        closed.periods.fast,
        closed.periods.slow,
        closed.periods.signal,
    );
    for (name, value) in [
        ("macd", closed.macd),
        ("signal", closed.signal),
        ("histogram", closed.histogram),
        ("realized_volatility_pct", closed.realized_volatility_pct),
    ] {
        if let Some(value) = value {
            line.push_str(&format!(",{name}={value}"));
        }
    }
    line.push_str(&format!(" {}\n", closed.bar.start_ms * 1_000_000));
    line
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{analyze, Candle, ChartRequest};

    // Monday 28 Sep 2026, 09:30 IST.
    const MONDAY_0930_MS: i64 = 1_790_568_000_000;

    fn tick(minute_offset: i64, second: i64, ltp: f64, volume: i64) -> BusTick {
        BusTick {
            v: 1,
            seq: minute_offset * 100 + second,
            symbol: "NSE:X".into(),
            ltp,
            volume,
            exchange_ts_ms: MONDAY_0930_MS + minute_offset * MINUTE_MS + second * 1000,
            gateway_ts_ms: 0,
        }
    }

    fn near(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-9, "{a} != {b}");
    }

    #[test]
    fn session_window_is_ist_weekdays_0915_to_1530() {
        assert!(in_session(MONDAY_0930_MS));
        assert!(!in_session(MONDAY_0930_MS - 16 * MINUTE_MS)); // 09:14
        assert!(in_session(MONDAY_0930_MS - 15 * MINUTE_MS)); // 09:15
        assert!(!in_session(MONDAY_0930_MS + 360 * MINUTE_MS)); // 15:30
        assert!(!in_session(MONDAY_0930_MS - 2 * 86_400_000)); // Saturday
    }

    #[test]
    fn ticks_build_bars_and_the_next_minute_closes_them() {
        let periods = MacdPeriods::default();
        let mut state = SymbolState::new("NSE:X");
        assert!(state.on_tick(&tick(0, 1, 100.0, 1_000), periods).closed.is_none());
        assert!(state.on_tick(&tick(0, 20, 103.0, 1_400), periods).closed.is_none());
        assert!(state.on_tick(&tick(0, 59, 99.0, 1_500), periods).closed.is_none());
        let closed = state.on_tick(&tick(1, 2, 101.0, 1_600), periods).closed.expect("minute rolled");
        assert_eq!((closed.bar.open, closed.bar.high, closed.bar.low, closed.bar.close), (100.0, 103.0, 99.0, 99.0));
        assert_eq!((closed.bar.volume, closed.bar.ticks), (500, 3));
        assert_eq!(state.bars_closed, 1);
        // A late print for the closed minute does not reopen it.
        assert!(!state.on_tick(&tick(0, 50, 500.0, 1_700), periods).recorded);
        assert_eq!(state.late_ticks, 1);
    }

    #[test]
    fn live_macd_matches_the_batch_calculation() {
        let periods = MacdPeriods { fast: 5, slow: 10, signal: 3 };
        let closes = [100.0, 101.0, 99.5, 102.0, 103.0, 101.0, 104.0, 105.5, 104.0, 106.0];
        let mut state = SymbolState::new("NSE:X");
        for (minute, close) in closes.iter().enumerate() {
            state.on_tick(&tick(minute as i64, 0, *close, 0), periods);
        }
        state.sweep(MONDAY_0930_MS + 20 * MINUTE_MS, 5_000, periods);
        let batch = analyze(ChartRequest {
            symbol: "NSE:X".into(),
            timeframe_seconds: 60,
            candles: closes.iter().enumerate().map(|(i, close)| Candle { timestamp: i as i64 * 60, close: *close }).collect(),
            macd_periods: periods,
        })
        .unwrap();
        let view = state.view(periods);
        near(view.macd.unwrap(), batch.macd.unwrap());
        near(view.signal.unwrap(), batch.signal.unwrap());
        near(view.realized_volatility_pct.unwrap(), batch.realized_volatility_pct.unwrap());
        assert!(view.warm == (10 >= periods.slow + periods.signal));
    }

    #[test]
    fn seed_rebuilds_history_and_keeps_bars_closed_since() {
        let periods = MacdPeriods { fast: 3, slow: 6, signal: 2 };
        let mut live = SymbolState::new("NSE:X");
        live.on_tick(&tick(0, 0, 110.0, 0), periods);
        live.on_tick(&tick(1, 0, 111.0, 0), periods); // closes minute 0 at 110
        let first_minute_s = MONDAY_0930_MS / 1000;
        let history: Vec<(i64, f64)> = (1..=5).map(|k| (first_minute_s - k * 60, 100.0 + k as f64)).collect();
        live.apply_seed(&history, periods);
        assert_eq!(live.seed, Seed::Seeded { bars: 5 });
        assert_eq!(live.bars_closed, 6); // five stored bars, then the one closed live

        let mut reference = SymbolState::new("NSE:X");
        let mut ordered = history.clone();
        ordered.sort_by_key(|(seconds, _)| *seconds);
        for (seconds, close) in ordered.iter().chain([(first_minute_s, 110.0)].iter()) {
            reference.record_close(seconds / 60, *close, periods);
        }
        near(live.view(periods).macd.unwrap(), reference.view(periods).macd.unwrap());
        // The minute still forming is left alone.
        assert_eq!(live.view(periods).bar.unwrap().close, 111.0);
    }

    #[test]
    fn off_session_prints_update_price_but_not_bars() {
        let periods = MacdPeriods::default();
        let mut state = SymbolState::new("NSE:X");
        let mut late = tick(0, 0, 90.0, 0);
        late.exchange_ts_ms = MONDAY_0930_MS + 361 * MINUTE_MS; // 15:31
        assert!(!state.on_tick(&late, periods).recorded);
        assert_eq!((state.ltp, state.off_session_ticks, state.view(periods).bar), (90.0, 1, None));
    }

    #[test]
    fn republished_last_trades_move_price_but_build_no_bar() {
        let periods = MacdPeriods::default();
        let mut state = SymbolState::new("NSE:X");
        let mut replay = tick(0, 58, 88.0, 0); // Monday 09:30:58 trade...
        replay.gateway_ts_ms = replay.exchange_ts_ms + 2 * 86_400_000; // ...received on Wednesday
        let outcome = state.on_tick(&replay, periods);
        assert!(!outcome.recorded && outcome.closed.is_none());
        assert_eq!((state.ltp, state.stale_ticks, state.view(periods).bar), (88.0, 1, None));
        let mut fresh = tick(1, 0, 89.0, 0);
        fresh.gateway_ts_ms = fresh.exchange_ts_ms + 800; // normal feed latency
        assert!(state.on_tick(&fresh, periods).recorded);
    }

    #[test]
    fn shards_are_stable_and_spread() {
        assert_eq!(shard_for("NSE:RELIANCE-EQ", 4), shard_for("NSE:RELIANCE-EQ", 4));
        for (shards, name) in [(4, "NSE:S{i}"), (8, "NSE:LOAD{i:04}26OCT{j}CE")] {
            let mut seen = vec![0usize; shards];
            for i in 0..1600 {
                let symbol = name.replace("{i}", &i.to_string()).replace("{i:04}", &format!("{i:04}")).replace("{j}", &(1000 + i).to_string());
                seen[shard_for(&symbol, shards)] += 1;
            }
            let fair = 1600 / shards;
            assert!(seen.iter().all(|count| *count > fair * 3 / 4 && *count < fair * 5 / 4), "{name}: {seen:?}");
        }
    }

    #[test]
    fn line_protocol_uses_exchange_time() {
        let t = tick(0, 1, 100.5, 42);
        assert_eq!(
            tick_line(&t),
            format!("ticks,symbol=NSE:X ltp=100.5,volume=42i,seq=1i,gateway_lag_ms={}i {}\n", -t.exchange_ts_ms, t.exchange_ts_ms * 1_000_000)
        );
        let mut state = SymbolState::new("NSE:X");
        state.on_tick(&t, MacdPeriods::default());
        let closed = state.sweep(t.exchange_ts_ms + 2 * MINUTE_MS, 0, MacdPeriods::default()).unwrap();
        let line = bar_line(&closed);
        assert!(line.starts_with("bars_1m,symbol=NSE:X open=100.5,"));
        assert!(line.ends_with(&format!(" {}\n", MONDAY_0930_MS * 1_000_000)));
        assert!(!line.contains("realized_volatility_pct")); // one bar has no return
    }
}
