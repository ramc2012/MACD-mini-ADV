use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub mod live;
pub mod minihttp;
pub mod nats;
pub mod pipeline;

pub const TRADING_SECONDS_PER_DAY: f64 = 22_500.0;
pub const TRADING_DAYS_PER_YEAR: f64 = 252.0;

/// Escape a QuestDB line-protocol tag value.
pub fn escape_tag(value: &str) -> String {
    value.replace('\\', "\\\\").replace(',', "\\,").replace(' ', "\\ ").replace('=', "\\=")
}

/// The original engine's `/api/chart/{symbol}` response. Its additional
/// `indicators` field and each candle's OHLCV fields are intentionally ignored.
#[derive(Debug, Deserialize)]
pub struct ChartRequest {
    pub symbol: String,
    pub timeframe_seconds: u32,
    pub candles: Vec<Candle>,
    /// The engine's configured MACD periods. Older engines omit them, and
    /// the engine's defaults apply.
    #[serde(default)]
    pub macd_periods: MacdPeriods,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct MacdPeriods {
    pub fast: usize,
    pub slow: usize,
    pub signal: usize,
}

impl Default for MacdPeriods {
    fn default() -> Self {
        Self { fast: 12, slow: 26, signal: 9 }
    }
}

#[derive(Debug, Deserialize)]
pub struct Candle {
    pub timestamp: i64,
    pub close: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct Analysis {
    pub symbol: String,
    pub timeframe_seconds: u32,
    pub candle_count: usize,
    pub last_timestamp: Option<i64>,
    pub last_close: Option<f64>,
    pub ema_fast: Option<f64>,
    pub ema_slow: Option<f64>,
    pub macd: Option<f64>,
    pub signal: Option<f64>,
    pub histogram: Option<f64>,
    pub fast_period: usize,
    pub slow_period: usize,
    pub signal_period: usize,
    /// Annualized sample standard deviation of consecutive log returns × 100.
    pub realized_volatility_pct: Option<f64>,
    pub trend: &'static str,
}

#[derive(Debug, PartialEq, Eq)]
pub enum AnalyzeError {
    EmptySymbol,
    InvalidTimeframe,
    InvalidClose,
    InvalidPeriods,
}

impl AnalyzeError {
    pub fn message(&self) -> &'static str {
        match self {
            Self::EmptySymbol => "symbol must not be empty",
            Self::InvalidTimeframe => "timeframe_seconds must be greater than zero",
            Self::InvalidClose => "every candle close must be positive and finite",
            Self::InvalidPeriods => "MACD periods must be positive with fast below slow",
        }
    }
}

/// Mirrors the original engine's EMA initialization: the first close seeds
/// both EMAs, and the first MACD value seeds the signal EMA.
pub fn analyze(request: ChartRequest) -> Result<Analysis, AnalyzeError> {
    let symbol = request.symbol.trim().to_owned();
    if symbol.is_empty() {
        return Err(AnalyzeError::EmptySymbol);
    }
    if request.timeframe_seconds == 0 {
        return Err(AnalyzeError::InvalidTimeframe);
    }
    let periods = request.macd_periods;
    if periods.fast == 0 || periods.signal == 0 || periods.fast >= periods.slow {
        return Err(AnalyzeError::InvalidPeriods);
    }

    // A chart is normally sorted already. Canonicalizing here also protects
    // the calculation from repeated live candles at the same timestamp.
    let mut by_timestamp = BTreeMap::new();
    for candle in request.candles {
        if !candle.close.is_finite() || candle.close <= 0.0 {
            return Err(AnalyzeError::InvalidClose);
        }
        by_timestamp.insert(candle.timestamp, candle.close);
    }
    let points: Vec<(i64, f64)> = by_timestamp.iter().map(|(timestamp, close)| (*timestamp, *close)).collect();
    let closes: Vec<f64> = by_timestamp.values().copied().collect();
    let mut result = Analysis {
        symbol,
        timeframe_seconds: request.timeframe_seconds,
        candle_count: closes.len(),
        last_timestamp: by_timestamp.last_key_value().map(|(timestamp, _)| *timestamp),
        last_close: closes.last().copied(),
        ema_fast: None,
        ema_slow: None,
        macd: None,
        signal: None,
        histogram: None,
        fast_period: periods.fast,
        slow_period: periods.slow,
        signal_period: periods.signal,
        realized_volatility_pct: realized_volatility(&points, request.timeframe_seconds),
        trend: "no_data",
    };

    let mut fast: Option<f64> = None;
    let mut slow: Option<f64> = None;
    let mut signal: Option<f64> = None;
    for close in closes {
        fast = Some(ema_step(fast, close, periods.fast));
        slow = Some(ema_step(slow, close, periods.slow));
        let macd = fast.unwrap() - slow.unwrap();
        signal = Some(ema_step(signal, macd, periods.signal));
    }
    if let (Some(fast), Some(slow), Some(signal)) = (fast, slow, signal) {
        let macd = fast - slow;
        result.ema_fast = Some(fast);
        result.ema_slow = Some(slow);
        result.macd = Some(macd);
        result.signal = Some(signal);
        result.histogram = Some(macd - signal);
        result.trend = if macd > 1e-10 {
            "bullish"
        } else if macd < -1e-10 {
            "bearish"
        } else {
            "neutral"
        };
    }
    Ok(result)
}

fn ema_step(previous: Option<f64>, current: f64, period: usize) -> f64 {
    match previous {
        Some(previous) => previous + (2.0 / (period as f64 + 1.0)) * (current - previous),
        None => current,
    }
}

fn realized_volatility(points: &[(i64, f64)], timeframe_seconds: u32) -> Option<f64> {
    let intraday = timeframe_seconds < 86_400;
    // Subtracting logs avoids underflow/overflow for very different prices.
    // An intraday return is only one bar's move when the bars are adjacent:
    // the overnight gap or a hole in the stored history spans far more time,
    // and annualizing it as a single bar overstates volatility.
    let returns: Vec<f64> = points
        .windows(2)
        .filter(|pair| !intraday || pair[1].0 - pair[0].0 == i64::from(timeframe_seconds))
        .map(|pair| pair[1].1.ln() - pair[0].1.ln())
        .collect();
    // Two returns are the minimum needed for a sample standard deviation.
    if returns.len() < 2 {
        return None;
    }
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let sum_squared = returns.iter().map(|value| (value - mean).powi(2)).sum::<f64>();
    let sample_std = (sum_squared / (returns.len() - 1) as f64).sqrt();
    let annual_bars = if !intraday {
        TRADING_DAYS_PER_YEAR * 86_400.0 / timeframe_seconds as f64
    } else {
        TRADING_DAYS_PER_YEAR * TRADING_SECONDS_PER_DAY / timeframe_seconds as f64
    };
    Some(sample_std * annual_bars.sqrt() * 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(closes: &[f64]) -> ChartRequest {
        ChartRequest {
            symbol: "NSE:NIFTY50-INDEX".into(),
            timeframe_seconds: 60,
            candles: closes.iter().enumerate().map(|(i, close)| Candle {
                timestamp: i as i64 * 60,
                close: *close,
            }).collect(),
            macd_periods: MacdPeriods::default(),
        }
    }

    fn near(actual: f64, expected: f64) {
        assert!((actual - expected).abs() < 1e-9, "actual={actual}, expected={expected}");
    }

    #[test]
    fn empty_history_has_null_metrics() {
        let result = analyze(request(&[])).unwrap();
        assert_eq!(result.candle_count, 0);
        assert!(result.last_close.is_none());
        assert!(result.macd.is_none());
        assert!(result.realized_volatility_pct.is_none());
        assert_eq!(result.trend, "no_data");
    }

    #[test]
    fn ema_macd_and_signal_match_original_engine_seed_rules() {
        let result = analyze(request(&[10.0, 20.0, 30.0])).unwrap();
        let fast_2 = 10.0 + (2.0 / 13.0) * 10.0;
        let fast_3 = fast_2 + (2.0 / 13.0) * (30.0 - fast_2);
        let slow_2 = 10.0 + (2.0 / 27.0) * 10.0;
        let slow_3 = slow_2 + (2.0 / 27.0) * (30.0 - slow_2);
        let macd_2 = fast_2 - slow_2;
        let macd_3 = fast_3 - slow_3;
        let signal_2 = 0.0 + 0.2 * macd_2;
        let signal_3 = signal_2 + 0.2 * (macd_3 - signal_2);
        near(result.ema_fast.unwrap(), fast_3);
        near(result.ema_slow.unwrap(), slow_3);
        near(result.macd.unwrap(), macd_3);
        near(result.signal.unwrap(), signal_3);
        near(result.histogram.unwrap(), macd_3 - signal_3);
        assert_eq!(result.trend, "bullish");
    }

    #[test]
    fn volatility_uses_sample_log_returns_and_annualization() {
        let mut chart = request(&[100.0, 110.0, 99.0]);
        chart.timeframe_seconds = 86_400;
        let result = analyze(chart).unwrap();
        let r1 = (110.0_f64 / 100.0).ln();
        let r2 = (99.0_f64 / 110.0).ln();
        let mean = (r1 + r2) / 2.0;
        let sample_std = ((r1 - mean).powi(2) + (r2 - mean).powi(2)).sqrt();
        near(result.realized_volatility_pct.unwrap(), sample_std * 252.0_f64.sqrt() * 100.0);
    }

    #[test]
    fn timestamps_are_sorted_and_duplicate_timestamp_uses_last_value() {
        let chart: ChartRequest = serde_json::from_str(r#"{
            "symbol":"NSE:TEST","timeframe_seconds":60,
            "candles":[
                {"timestamp":120,"close":12,"open":10},
                {"timestamp":60,"close":10},
                {"timestamp":120,"close":13}
            ],"indicators":[]
        }"#).unwrap();
        let result = analyze(chart).unwrap();
        assert_eq!(result.candle_count, 2);
        assert_eq!(result.last_timestamp, Some(120));
        assert_eq!(result.last_close, Some(13.0));
    }

    #[test]
    fn engine_periods_replace_the_defaults() {
        let mut chart = request(&[10.0, 20.0]);
        chart.macd_periods = MacdPeriods { fast: 5, slow: 10, signal: 3 };
        let result = analyze(chart).unwrap();
        let fast = 10.0 + (2.0 / 6.0) * 10.0;
        let slow = 10.0 + (2.0 / 11.0) * 10.0;
        near(result.ema_fast.unwrap(), fast);
        near(result.ema_slow.unwrap(), slow);
        near(result.signal.unwrap(), 0.5 * (fast - slow));
        assert_eq!((result.fast_period, result.slow_period, result.signal_period), (5, 10, 3));
    }

    #[test]
    fn missing_periods_use_engine_defaults_and_bad_periods_are_rejected() {
        let chart: ChartRequest = serde_json::from_str(
            r#"{"symbol":"NSE:TEST","timeframe_seconds":60,"candles":[]}"#).unwrap();
        assert_eq!(chart.macd_periods, MacdPeriods { fast: 12, slow: 26, signal: 9 });
        let mut chart = request(&[10.0]);
        chart.macd_periods = MacdPeriods { fast: 26, slow: 12, signal: 9 };
        assert_eq!(analyze(chart).unwrap_err(), AnalyzeError::InvalidPeriods);
    }

    #[test]
    fn intraday_volatility_skips_overnight_and_missing_bar_gaps() {
        let bars = |points: &[(i64, f64)]| ChartRequest {
            symbol: "NSE:TEST".into(),
            timeframe_seconds: 60,
            candles: points.iter().map(|(timestamp, close)| Candle { timestamp: *timestamp, close: *close }).collect(),
            macd_periods: MacdPeriods::default(),
        };
        // Same session moves, then a 10% overnight gap that is not a 1-minute return.
        let with_gap = analyze(bars(&[(0, 100.0), (60, 101.0), (120, 100.0), (86_400, 110.0), (86_460, 111.1)])).unwrap();
        let session = analyze(bars(&[(0, 100.0), (60, 101.0), (120, 100.0), (180, 101.0)])).unwrap();
        let r1 = (101.0_f64 / 100.0).ln();
        let r2 = (100.0_f64 / 101.0).ln();
        let r3 = (111.1_f64 / 110.0).ln();
        let mean = (r1 + r2 + r3) / 3.0;
        let std = (((r1 - mean).powi(2) + (r2 - mean).powi(2) + (r3 - mean).powi(2)) / 2.0).sqrt();
        near(with_gap.realized_volatility_pct.unwrap(), std * (252.0_f64 * 22_500.0 / 60.0).sqrt() * 100.0);
        assert!(with_gap.realized_volatility_pct.unwrap() < 2.0 * session.realized_volatility_pct.unwrap());
        // No adjacent pair at all: nothing honest to report.
        assert!(analyze(bars(&[(0, 100.0), (600, 101.0), (1_200, 102.0)])).unwrap().realized_volatility_pct.is_none());
    }

    #[test]
    fn invalid_prices_are_rejected() {
        assert_eq!(analyze(request(&[10.0, 0.0])).unwrap_err(), AnalyzeError::InvalidClose);
    }
}
