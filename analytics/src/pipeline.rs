//! The live pipeline: tick bus -> symbol shards (parallel) -> QuestDB.
//!
//! One task reads the bus and routes each tick by symbol to one of N worker
//! shards. Shards own disjoint symbols, so they run on separate cores without
//! contending, and a symbol's ticks stay in order. A seeder loads each new
//! symbol's stored minute bars from the engine, two at a time, and the worker
//! applies them in line with that symbol's ticks. QuestDB receives raw ticks
//! and closed bars through one batched line-protocol connection.

use crate::{
    live::{bar_line, shard_for, tick_line, BusTick, LiveView, Seed, SymbolState},
    minihttp, nats, ChartRequest, MacdPeriods,
};
use serde::Deserialize;
use serde_json::json;
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering::Relaxed},
        Arc, Mutex, RwLock,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    io::{AsyncWriteExt, BufWriter},
    net::TcpStream,
    sync::{mpsc, Semaphore},
};

const SHARD_QUEUE: usize = 16_384;
const QUESTDB_QUEUE: usize = 131_072;
const SWEEP_GRACE_MS: i64 = 5_000;
const TICK_SUBJECT: &str = "md.tick.>";

#[derive(Clone, Debug)]
pub struct Config {
    pub nats_addr: String,
    /// `host:port` of the engine, for seeding history and reading periods.
    pub engine: Option<String>,
    pub token: Option<String>,
    pub questdb_ilp: Option<String>,
    pub questdb_http: Option<String>,
    pub workers: usize,
    pub store_ticks: bool,
}

pub enum WorkerMsg {
    Tick(Vec<u8>),
    Seed { symbol: String, result: Result<Vec<(i64, f64)>, String> },
    Sweep(i64),
    Reset,
}

type Shard = Arc<Mutex<HashMap<String, SymbolState>>>;

#[derive(Default)]
pub struct Stats {
    bus_connected: AtomicBool,
    bus_connects: AtomicU64,
    bus_messages: AtomicU64,
    bus_dropped: AtomicU64,
    parse_errors: AtomicU64,
    bars_closed: AtomicU64,
    seeds_ok: AtomicU64,
    seeds_failed: AtomicU64,
    seeds_deferred: AtomicU64,
    questdb_connected: AtomicBool,
    questdb_tables_ready: AtomicBool,
    questdb_lines: AtomicU64,
    questdb_dropped: AtomicU64,
    last_error: Mutex<String>,
}

impl Stats {
    fn error(&self, message: String) {
        eprintln!("live pipeline: {message}");
        *self.last_error.lock().unwrap() = message;
    }
}

pub struct Pipeline {
    shards: Vec<Shard>,
    shard_ticks: Vec<Arc<AtomicU64>>,
    periods: Arc<RwLock<MacdPeriods>>,
    stats: Arc<Stats>,
    workers: usize,
}

fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or_default()
}

impl Pipeline {
    pub fn start(config: Config) -> Arc<Self> {
        let workers = config.workers.max(1);
        let stats = Arc::new(Stats::default());
        let periods = Arc::new(RwLock::new(MacdPeriods::default()));
        let shards: Vec<Shard> = (0..workers).map(|_| Arc::new(Mutex::new(HashMap::new()))).collect();
        let shard_ticks: Vec<Arc<AtomicU64>> = (0..workers).map(|_| Arc::new(AtomicU64::new(0))).collect();
        let (questdb_tx, questdb_rx) = mpsc::channel::<String>(QUESTDB_QUEUE);
        let questdb = config.questdb_ilp.as_ref().map(|_| questdb_tx);
        let (seed_tx, seed_rx) = mpsc::channel::<(usize, String)>(8_192);

        let mut senders = Vec::with_capacity(workers);
        for index in 0..workers {
            let (tx, rx) = mpsc::channel(SHARD_QUEUE);
            senders.push(tx);
            tokio::spawn(worker(
                index,
                rx,
                shards[index].clone(),
                shard_ticks[index].clone(),
                periods.clone(),
                stats.clone(),
                config.engine.is_some().then(|| seed_tx.clone()),
                questdb.clone(),
                config.store_ticks,
            ));
        }
        if let Some(address) = config.questdb_ilp.clone() {
            tokio::spawn(questdb_writer(questdb_rx, address, config.questdb_http.clone(), stats.clone()));
        }
        if let Some(engine) = config.engine.clone() {
            tokio::spawn(seeder(seed_rx, senders.clone(), engine.clone(), config.token.clone(), stats.clone()));
            tokio::spawn(period_watch(engine, config.token.clone(), periods.clone(), senders.clone(), stats.clone()));
        }
        tokio::spawn(sweeper(senders.clone()));
        tokio::spawn(bus(config.nats_addr.clone(), senders, stats.clone()));
        Arc::new(Self { shards, shard_ticks, periods, stats, workers })
    }

    pub fn view(&self, symbol: &str) -> Option<LiveView> {
        let periods = *self.periods.read().unwrap();
        let shard = self.shards[shard_for(symbol, self.workers)].lock().unwrap();
        shard.get(symbol).map(|state| state.view(periods))
    }

    pub fn scan(&self, sort: &str, limit: usize, warm_only: bool) -> Vec<LiveView> {
        let periods = *self.periods.read().unwrap();
        let mut rows: Vec<LiveView> = Vec::new();
        for shard in &self.shards {
            let shard = shard.lock().unwrap();
            rows.extend(shard.values().map(|state| state.view(periods)).filter(|view| !warm_only || view.warm));
        }
        let key = |view: &LiveView| -> f64 {
            match sort {
                "histogram" => view.histogram.unwrap_or(f64::NEG_INFINITY),
                "volatility" => view.realized_volatility_pct.unwrap_or(f64::NEG_INFINITY),
                "ticks" => view.ticks as f64,
                _ => view.histogram.map(f64::abs).unwrap_or(f64::NEG_INFINITY), // abs_histogram
            }
        };
        rows.sort_by(|a, b| key(b).total_cmp(&key(a)).then_with(|| a.symbol.cmp(&b.symbol)));
        rows.truncate(limit);
        rows
    }

    pub fn stats(&self) -> serde_json::Value {
        let s = &self.stats;
        let mut seeds = HashMap::<&str, u64>::new();
        let mut symbols = Vec::with_capacity(self.workers);
        for shard in &self.shards {
            let shard = shard.lock().unwrap();
            symbols.push(shard.len());
            for state in shard.values() {
                let key = match state.seed {
                    Seed::Unrequested => "unrequested",
                    Seed::Pending => "pending",
                    Seed::Seeded { .. } => "seeded",
                    Seed::Failed { .. } => "failed",
                };
                *seeds.entry(key).or_default() += 1;
            }
        }
        json!({
            "workers": self.workers,
            "periods": *self.periods.read().unwrap(),
            "shards": (0..self.workers).map(|i| json!({
                "symbols": symbols[i], "ticks": self.shard_ticks[i].load(Relaxed),
            })).collect::<Vec<_>>(),
            "bus": {
                "subject": TICK_SUBJECT,
                "connected": s.bus_connected.load(Relaxed),
                "connects": s.bus_connects.load(Relaxed),
                "messages": s.bus_messages.load(Relaxed),
                "dropped": s.bus_dropped.load(Relaxed),
                "parse_errors": s.parse_errors.load(Relaxed),
            },
            "bars_closed": s.bars_closed.load(Relaxed),
            "seeds": {
                "by_state": seeds, "loaded": s.seeds_ok.load(Relaxed),
                "failed": s.seeds_failed.load(Relaxed), "deferred": s.seeds_deferred.load(Relaxed),
            },
            "questdb": {
                "connected": s.questdb_connected.load(Relaxed),
                "tables_ready": s.questdb_tables_ready.load(Relaxed),
                "lines": s.questdb_lines.load(Relaxed),
                "dropped": s.questdb_dropped.load(Relaxed),
            },
            "last_error": s.last_error.lock().unwrap().clone(),
        })
    }
}

#[allow(clippy::too_many_arguments)]
async fn worker(
    index: usize,
    mut rx: mpsc::Receiver<WorkerMsg>,
    shard: Shard,
    ticks: Arc<AtomicU64>,
    periods: Arc<RwLock<MacdPeriods>>,
    stats: Arc<Stats>,
    seeds: Option<mpsc::Sender<(usize, String)>>,
    questdb: Option<mpsc::Sender<String>>,
    store_ticks: bool,
) {
    let emit = |line: String| {
        if let Some(questdb) = &questdb {
            if questdb.try_send(line).is_err() {
                stats.questdb_dropped.fetch_add(1, Relaxed);
            }
        }
    };
    while let Some(message) = rx.recv().await {
        let periods = *periods.read().unwrap();
        match message {
            WorkerMsg::Tick(payload) => {
                let tick: BusTick = match serde_json::from_slice(&payload) {
                    Ok(tick) => tick,
                    Err(_) => {
                        stats.parse_errors.fetch_add(1, Relaxed);
                        continue;
                    }
                };
                ticks.fetch_add(1, Relaxed);
                let (outcome, request_seed) = {
                    let mut shard = shard.lock().unwrap();
                    let state = shard.entry(tick.symbol.clone()).or_insert_with(|| SymbolState::new(&tick.symbol));
                    let request_seed = seeds.is_some() && state.seed == Seed::Unrequested;
                    if request_seed {
                        state.seed = Seed::Pending;
                    }
                    (state.on_tick(&tick, periods), request_seed)
                };
                if request_seed {
                    if let Some(seeds) = &seeds {
                        if seeds.try_send((index, tick.symbol.clone())).is_err() {
                            // Seeder backlog is full; ask again on a later tick.
                            stats.seeds_deferred.fetch_add(1, Relaxed);
                            if let Some(state) = shard.lock().unwrap().get_mut(&tick.symbol) {
                                state.seed = Seed::Unrequested;
                            }
                        }
                    }
                }
                if store_ticks && outcome.recorded {
                    emit(tick_line(&tick));
                }
                if let Some(closed) = outcome.closed {
                    stats.bars_closed.fetch_add(1, Relaxed);
                    emit(bar_line(&closed));
                }
            }
            WorkerMsg::Seed { symbol, result } => {
                let mut shard = shard.lock().unwrap();
                if let Some(state) = shard.get_mut(&symbol) {
                    match result {
                        Ok(history) => {
                            state.apply_seed(&history, periods);
                            stats.seeds_ok.fetch_add(1, Relaxed);
                        }
                        Err(error) => {
                            state.seed = Seed::Failed { error };
                            stats.seeds_failed.fetch_add(1, Relaxed);
                        }
                    }
                }
            }
            WorkerMsg::Sweep(now) => {
                let closed: Vec<_> = {
                    let mut shard = shard.lock().unwrap();
                    shard.values_mut().filter_map(|state| state.sweep(now, SWEEP_GRACE_MS, periods)).collect()
                };
                for bar in closed {
                    stats.bars_closed.fetch_add(1, Relaxed);
                    emit(bar_line(&bar));
                }
            }
            WorkerMsg::Reset => {
                let mut shard = shard.lock().unwrap();
                for state in shard.values_mut() {
                    state.reset_indicators();
                    state.seed = Seed::Unrequested; // re-seeded on its next tick
                }
            }
        }
    }
}

async fn bus(address: String, senders: Vec<mpsc::Sender<WorkerMsg>>, stats: Arc<Stats>) {
    let shards = senders.len();
    loop {
        stats.bus_connects.fetch_add(1, Relaxed);
        let ready = || stats.bus_connected.store(true, Relaxed);
        let result = nats::subscribe(&address, TICK_SUBJECT, ready, |subject, payload| {
            stats.bus_messages.fetch_add(1, Relaxed);
            let symbol = subject.strip_prefix("md.tick.").unwrap_or(subject);
            // Never wait on a slow shard: the bus must keep draining.
            if senders[shard_for(symbol, shards)].try_send(WorkerMsg::Tick(payload)).is_err() {
                stats.bus_dropped.fetch_add(1, Relaxed);
            }
        })
        .await;
        stats.bus_connected.store(false, Relaxed);
        if let Err(error) = result {
            stats.error(format!("bus {address}: {error}"));
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

async fn sweeper(senders: Vec<mpsc::Sender<WorkerMsg>>) {
    let mut interval = tokio::time::interval(Duration::from_secs(5));
    loop {
        interval.tick().await;
        let now = now_ms();
        for sender in &senders {
            let _ = sender.try_send(WorkerMsg::Sweep(now));
        }
    }
}

fn auth_headers(token: &Option<String>) -> Vec<(&'static str, &str)> {
    token.as_deref().map(|token| vec![("X-Macd-Token", token)]).unwrap_or_default()
}

async fn fetch_history(engine: &str, symbol: &str, token: &Option<String>) -> Result<Vec<(i64, f64)>, String> {
    let path = format!("/api/chart/{}?timeframe_seconds=60", minihttp::encode(symbol, true));
    let (status, body) = minihttp::get(engine, &path, &auth_headers(token), Duration::from_secs(20)).await?;
    if status != 200 {
        return Err(format!("engine chart HTTP {status}"));
    }
    let chart: ChartRequest = serde_json::from_slice(&body).map_err(|error| format!("chart JSON: {error}"))?;
    Ok(chart.candles.into_iter().map(|candle| (candle.timestamp, candle.close)).collect())
}

async fn seeder(
    mut rx: mpsc::Receiver<(usize, String)>,
    senders: Vec<mpsc::Sender<WorkerMsg>>,
    engine: String,
    token: Option<String>,
    stats: Arc<Stats>,
) {
    // Two at a time: each is a small indexed SQLite read on the engine, and
    // the engine's own loop must stay free for ticks.
    let slots = Arc::new(Semaphore::new(2));
    while let Some((shard, symbol)) = rx.recv().await {
        let permit = slots.clone().acquire_owned().await.expect("semaphore closed");
        let (engine, token, sender, stats) = (engine.clone(), token.clone(), senders[shard].clone(), stats.clone());
        tokio::spawn(async move {
            let result = fetch_history(&engine, &symbol, &token).await;
            if let Err(error) = &result {
                stats.error(format!("seed {symbol}: {error}"));
            }
            let _ = sender.send(WorkerMsg::Seed { symbol, result }).await;
            drop(permit);
        });
    }
}

#[derive(Deserialize)]
struct EnginePeriods {
    fast_period: usize,
    slow_period: usize,
    signal_period: usize,
}

async fn period_watch(
    engine: String,
    token: Option<String>,
    periods: Arc<RwLock<MacdPeriods>>,
    senders: Vec<mpsc::Sender<WorkerMsg>>,
    stats: Arc<Stats>,
) {
    let mut interval = tokio::time::interval(Duration::from_secs(60));
    loop {
        interval.tick().await;
        let fetched = match minihttp::get(&engine, "/api/settings", &auth_headers(&token), Duration::from_secs(10)).await {
            Ok((200, body)) => serde_json::from_slice::<EnginePeriods>(&body).map_err(|error| error.to_string()),
            Ok((status, _)) => Err(format!("HTTP {status}")),
            Err(error) => Err(error),
        };
        match fetched {
            Ok(p) if p.fast_period >= 1 && p.signal_period >= 1 && p.fast_period < p.slow_period => {
                let next = MacdPeriods { fast: p.fast_period, slow: p.slow_period, signal: p.signal_period };
                let changed = {
                    let mut current = periods.write().unwrap();
                    let changed = *current != next;
                    *current = next;
                    changed
                };
                if changed {
                    for sender in &senders {
                        let _ = sender.send(WorkerMsg::Reset).await;
                    }
                }
            }
            Ok(_) => stats.error("engine reported invalid MACD periods".into()),
            Err(error) => stats.error(format!("engine settings: {error}")),
        }
    }
}

/// Tables are created before the first line is written, so line protocol
/// does not auto-create them without retention. Raw ticks expire with the
/// engine's own tick retention; bars are kept longer and de-duplicated.
pub const QUESTDB_TABLES: [&str; 2] = [
    "CREATE TABLE IF NOT EXISTS ticks (symbol SYMBOL, ltp DOUBLE, volume LONG, seq LONG, gateway_lag_ms LONG, ts TIMESTAMP) \
     TIMESTAMP(ts) PARTITION BY DAY TTL 5 DAYS WAL",
    "CREATE TABLE IF NOT EXISTS bars_1m (symbol SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume LONG, ticks LONG, \
     fast_period LONG, slow_period LONG, signal_period LONG, macd DOUBLE, signal DOUBLE, histogram DOUBLE, realized_volatility_pct DOUBLE, ts TIMESTAMP) \
     TIMESTAMP(ts) PARTITION BY DAY TTL 90 DAYS WAL DEDUP UPSERT KEYS(ts, symbol)",
];

async fn create_tables(http: &str, stats: &Stats) -> bool {
    for attempt in 0..60 {
        let mut ok = true;
        for ddl in QUESTDB_TABLES {
            let path = format!("/exec?query={}", minihttp::encode(ddl, false));
            match minihttp::get(http, &path, &[], Duration::from_secs(10)).await {
                Ok((200, _)) => {}
                Ok((status, body)) => {
                    ok = false;
                    stats.error(format!("questdb DDL HTTP {status}: {}", String::from_utf8_lossy(&body)));
                }
                Err(error) => {
                    ok = false;
                    if attempt % 10 == 0 {
                        stats.error(format!("questdb DDL: {error}"));
                    }
                }
            }
        }
        if ok {
            return true;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
    false
}

async fn questdb_writer(mut rx: mpsc::Receiver<String>, address: String, http: Option<String>, stats: Arc<Stats>) {
    if let Some(http) = http {
        let ready = create_tables(&http, &stats).await;
        stats.questdb_tables_ready.store(ready, Relaxed);
    }
    loop {
        let stream = match tokio::time::timeout(Duration::from_secs(2), TcpStream::connect(&address)).await {
            Ok(Ok(stream)) => stream,
            _ => {
                stats.questdb_connected.store(false, Relaxed);
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        stream.set_nodelay(true).ok();
        stats.questdb_connected.store(true, Relaxed);
        let mut writer = BufWriter::with_capacity(256 << 10, stream);
        let mut flush = tokio::time::interval(Duration::from_millis(200));
        loop {
            tokio::select! {
                line = rx.recv() => {
                    let Some(line) = line else { return };
                    if writer.write_all(line.as_bytes()).await.is_err() {
                        stats.questdb_dropped.fetch_add(1, Relaxed);
                        break;
                    }
                    stats.questdb_lines.fetch_add(1, Relaxed);
                }
                _ = flush.tick() => {
                    if writer.flush().await.is_err() {
                        break;
                    }
                }
            }
        }
        stats.questdb_connected.store(false, Relaxed);
        stats.error(format!("questdb {address}: connection lost; reconnecting"));
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}
