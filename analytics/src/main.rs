use axum::{
    extract::{DefaultBodyLimit, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use macd_analytics::{analyze, Analysis, ChartRequest};
use serde::Serialize;
use std::{env, net::Ipv4Addr, time::Duration};
use tokio::{io::AsyncWriteExt, net::TcpStream, sync::mpsc, time::timeout};

#[derive(Clone)]
struct AppState {
    questdb: Option<mpsc::Sender<Analysis>>,
}

#[derive(Serialize)]
struct ErrorResponse {
    error: &'static str,
}

#[tokio::main]
async fn main() {
    let port = env::var("PORT").ok().and_then(|value| value.parse::<u16>().ok()).unwrap_or(8081);
    let questdb = env::var("QUESTDB_ADDR").ok().filter(|value| !value.trim().is_empty()).map(|address| {
        let (sender, receiver) = mpsc::channel(512);
        tokio::spawn(questdb_writer(receiver, address));
        sender
    });
    let app = Router::new()
        .route("/health", get(health))
        .route("/analyze", post(analyze_chart))
        .layer(DefaultBodyLimit::max(32 * 1024 * 1024))
        .with_state(AppState { questdb });
    let listener = tokio::net::TcpListener::bind((Ipv4Addr::UNSPECIFIED, port)).await
        .expect("failed to bind analytics service");
    println!("analytics listening on {port}");
    axum::serve(listener, app).await.expect("analytics server failed");
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"ok": true}))
}

async fn analyze_chart(
    State(state): State<AppState>,
    Json(request): Json<ChartRequest>,
) -> Result<Json<Analysis>, (StatusCode, Json<ErrorResponse>)> {
    let result = analyze(request).map_err(|error| {
        (StatusCode::BAD_REQUEST, Json(ErrorResponse { error: error.message() }))
    })?;
    if result.candle_count > 0 {
        if let Some(sender) = &state.questdb {
            // Analytics remains available when QuestDB is slow, down or full.
            let _ = sender.try_send(result.clone());
        }
    }
    Ok(Json(result))
}

async fn questdb_writer(mut receiver: mpsc::Receiver<Analysis>, address: String) {
    while let Some(observation) = receiver.recv().await {
        let Some(line) = questdb_line(&observation) else { continue };
        if let Ok(Ok(mut connection)) = timeout(Duration::from_millis(300), TcpStream::connect(&address)).await {
            let _ = timeout(Duration::from_millis(500), connection.write_all(line.as_bytes())).await;
        }
    }
}

fn questdb_line(observation: &Analysis) -> Option<String> {
    let (Some(last_close), Some(ema_fast), Some(ema_slow), Some(macd), Some(signal), Some(histogram)) = (
        observation.last_close,
        observation.ema_fast,
        observation.ema_slow,
        observation.macd,
        observation.signal,
        observation.histogram,
    ) else {
        return None;
    };
    let mut line = format!(
        "macd_analytics,symbol={},timeframe_seconds={} candle_count={}i,last_timestamp={}i,last_close={},ema_fast={},ema_slow={},macd={},signal={},histogram={},fast_period={}i,slow_period={}i,signal_period={}i,trend=\"{}\"",
        escape_tag(&observation.symbol),
        observation.timeframe_seconds,
        observation.candle_count,
        observation.last_timestamp.unwrap_or_default(),
        last_close,
        ema_fast,
        ema_slow,
        macd,
        signal,
        histogram,
        observation.fast_period,
        observation.slow_period,
        observation.signal_period,
        observation.trend,
    );
    if let Some(volatility) = observation.realized_volatility_pct {
        line.push_str(&format!(",realized_volatility_pct={volatility}"));
    }
    line.push('\n');
    Some(line)
}

fn escape_tag(value: &str) -> String {
    value.replace('\\', "\\\\").replace(',', "\\,").replace(' ', "\\ ").replace('=', "\\=")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn questdb_record_escapes_symbol_and_omits_missing_volatility() {
        let observation = Analysis {
            symbol: "NSE:TEST, A=B".into(),
            timeframe_seconds: 60,
            candle_count: 1,
            last_timestamp: Some(123),
            last_close: Some(10.0),
            ema_fast: Some(10.0),
            ema_slow: Some(10.0),
            macd: Some(0.0),
            signal: Some(0.0),
            histogram: Some(0.0),
            fast_period: 12,
            slow_period: 26,
            signal_period: 9,
            realized_volatility_pct: None,
            trend: "neutral",
        };
        let line = questdb_line(&observation).unwrap();
        assert!(line.starts_with("macd_analytics,symbol=NSE:TEST\\,\\ A\\=B,timeframe_seconds=60 "));
        assert!(!line.contains("realized_volatility_pct"));
        assert!(line.ends_with('\n'));
    }
}
