import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, ColorType, type IChartApi, type Time } from 'lightweight-charts'
import { Activity, ArrowDownRight, ArrowUpRight, BarChart3, Check, ChevronDown, Clock3, KeyRound, LoaderCircle, Search, ShieldCheck, Wallet, X } from 'lucide-react'
import { BlastPanel, type SpotStatus } from './BlastPanel'

type Instrument = { symbol: string; sector: string }
type IndexGauge = { name: string; symbol: string; note: string }
type Quote = { symbol: string; price: number; previous_close: number; change_pct: number; volume: number; trade_at: string; observed_at: string; source: string }
type Account = { initial_capital: number; cash: number; equity: number; market_value: number; realized: number; unrealized: number; return_pct: number; estimated: boolean }
type Position = { symbol: string; quantity: number; average_cost: number; market_price: number; market_value: number; unrealized: number; mark_estimated: boolean }
type Fill = { id: number; at: string; symbol: string; side: string; quantity: number; price: number; notional: number; realized: number; source: string }
type Point = { at: string; equity: number }
type State = { account: Account; positions: Position[]; fills: Fill[]; equity_history: Point[]; universe: Instrument[]; indices: IndexGauge[]; quotes: Record<string, Quote>; feed: { status: string; error?: string; rest_error?: string; quotes: number; last_trade_at?: string; finnhub_configured: boolean; alpha_vantage_configured: boolean }; spot: SpotStatus; market: { open: boolean; now_et: string; session: string }; risk: { max_position_pct: number } }
type DailyBar = { day: string; open: number; high: number; low: number; close: number; volume: number }
type History = { symbol: string; source: string; fetched_at: string; bars: DailyBar[] }
type MinuteBar = { symbol: string; at: string; open: number; high: number; low: number; close: number; volume: number; source: string }
type MinuteHistory = { symbol: string; source: string; bars: MinuteBar[] }
type Timeframe = 'daily' | 'minute'

const dollars = (value?: number, digits = 2) => value == null ? '—' : value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits })
const number = (value?: number) => value == null ? '—' : value.toLocaleString('en-US')
const signed = (value?: number, digits = 2) => value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
const since = (iso?: string) => { if (!iso) return 'No update'; const sec = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000)); return sec < 60 ? `${sec}s ago` : sec < 3600 ? `${Math.floor(sec / 60)}m ago` : `${Math.floor(sec / 3600)}h ago` }
const tone = (value: number) => value > 0 ? 'positive' : value < 0 ? 'negative' : 'muted'

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', ...options })
  const body = await response.json()
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`)
  return body as T
}

function PriceChart({ daily, minute, timeframe, symbol }: { daily: History | null; minute: MinuteHistory | null; timeframe: Timeframe; symbol: string }) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!host.current) return
    const bars = timeframe === 'daily'
      ? daily?.bars.map(bar => ({ time: bar.day as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume }))
      : minute?.bars.map(bar => ({ time: Math.floor(Date.parse(bar.at) / 1000) as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume }))
    if (!bars?.length) return
    const chart: IChartApi = createChart(host.current, {
      width: host.current.clientWidth, height: host.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: '#0d1520' }, textColor: '#8b9aaa', fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: '#1b2937' }, horzLines: { color: '#1b2937' } },
      rightPriceScale: { borderColor: '#263645', scaleMargins: { top: 0.06, bottom: 0.24 } }, timeScale: { borderColor: '#263645', timeVisible: timeframe === 'minute', secondsVisible: false },
      crosshair: { vertLine: { color: '#61778c', labelBackgroundColor: '#25364a' }, horzLine: { color: '#61778c', labelBackgroundColor: '#25364a' } },
    })
    const series = chart.addSeries(CandlestickSeries, { upColor: '#26b891', downColor: '#e56d72', borderUpColor: '#26b891', borderDownColor: '#e56d72', wickUpColor: '#26b891', wickDownColor: '#e56d72' })
    series.setData(bars.map(bar => ({ time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close })))
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: '', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    volume.setData(bars.map(bar => ({ time: bar.time, value: bar.volume, color: bar.close >= bar.open ? '#2e8d78aa' : '#a95562aa' })))
    chart.timeScale().fitContent()
    const observer = new ResizeObserver(() => { if (host.current) { chart.applyOptions({ width: host.current.clientWidth, height: host.current.clientHeight }); chart.timeScale().fitContent() } })
    observer.observe(host.current)
    return () => { observer.disconnect(); chart.remove() }
  }, [daily, minute, timeframe, symbol])
  return <div className="chart-host" ref={host} />
}

function EquityChart({ points }: { points: Point[] }) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!host.current || points.length < 2) return
    const chart = createChart(host.current, {
      width: host.current.clientWidth, height: 164,
      layout: { background: { type: ColorType.Solid, color: '#121d29' }, textColor: '#8192a2', fontFamily: 'Inter, system-ui, sans-serif', fontSize: 10 },
      grid: { vertLines: { color: '#1d2d3d' }, horzLines: { color: '#1d2d3d' } },
      rightPriceScale: { borderColor: '#2a3c4d' }, timeScale: { borderColor: '#2a3c4d', timeVisible: true },
    })
    const line = chart.addSeries(LineSeries, { color: '#58c6b3', lineWidth: 2, priceLineVisible: false })
    const unique = new Map<number, number>()
    for (const point of points) unique.set(Math.floor(Date.parse(point.at) / 1000), point.equity)
    line.setData([...unique].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as Time, value })))
    chart.timeScale().fitContent()
    const observer = new ResizeObserver(() => { if (host.current) chart.applyOptions({ width: host.current.clientWidth }) })
    observer.observe(host.current)
    return () => { observer.disconnect(); chart.remove() }
  }, [points])
  return <div ref={host} className="equity-host" />
}

export default function App() {
  const [state, setState] = useState<State | null>(null)
  const [selected, setSelected] = useState('AAPL')
  const [query, setQuery] = useState('')
  const [sector, setSector] = useState('All sectors')
  const [history, setHistory] = useState<History | null>(null)
  const [minuteHistory, setMinuteHistory] = useState<MinuteHistory | null>(null)
  const [timeframe, setTimeframe] = useState<Timeframe>('daily')
  const historyCache = useRef(new Map<string, History>())
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const [historyBusy, setHistoryBusy] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [minuteError, setMinuteError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [finnhubKey, setFinnhubKey] = useState('')
  const [alphaKey, setAlphaKey] = useState('')
  const [settingsBusy, setSettingsBusy] = useState(false)
  const [automationBusy, setAutomationBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null)
  const [loadError, setLoadError] = useState('')

  const refresh = useCallback(async () => {
    try { const next = await api<State>('/api/state'); setState(next); setLoadError('') }
    catch (error) { setLoadError((error as Error).message) }
  }, [])
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer) }, [refresh])
  useEffect(() => {
    const controller = new AbortController()
    setHistory(historyCache.current.get(selected) || null)
    setMinuteHistory(null)
    setTimeframe('daily')
    setHistoryError('')
    setMinuteError('')
    setHistoryBusy(false)
    const loadMinute = async () => {
      try {
        const next = await api<MinuteHistory>(`/api/spot/bars?symbol=${encodeURIComponent(selected)}`, { signal: controller.signal })
        if (!controller.signal.aborted) setMinuteHistory(next)
      } catch (error) { if (!controller.signal.aborted) setMinuteError((error as Error).message) }
    }
    void loadMinute()
    const interval = window.setInterval(() => void loadMinute(), 30000)
    const timeout = window.setTimeout(async () => {
      if (!state?.feed.alpha_vantage_configured || historyCache.current.has(selected)) return
      setHistoryBusy(true)
      try {
        const next = await api<History>(`/api/history?symbol=${encodeURIComponent(selected)}`, { signal: controller.signal })
        historyCache.current.set(selected, next)
        if (!controller.signal.aborted) setHistory(next)
      } catch (error) { if (!controller.signal.aborted) setHistoryError((error as Error).message) }
      finally { if (!controller.signal.aborted) setHistoryBusy(false) }
    }, 250)
    return () => { controller.abort(); window.clearTimeout(timeout); window.clearInterval(interval) }
  }, [selected, state?.feed.alpha_vantage_configured])
  useEffect(() => { void api('/api/focus', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: selected }) }).catch(() => {}) }, [selected])
  const toggleAutomation = async () => {
    if (!state?.spot) return
    setAutomationBusy(true)
    try { await api('/api/spot/automation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !state.spot.auto_enabled }) }); await refresh() }
    catch (error) { setMessage({ text: (error as Error).message, ok: false }) }
    finally { setAutomationBusy(false) }
  }

  const instruments = state?.universe || []
  const sectors = useMemo(() => ['All sectors', ...new Set(instruments.map(item => item.sector))], [instruments])
  const filtered = useMemo(() => instruments.filter(item => (sector === 'All sectors' || item.sector === sector) && item.symbol.toLowerCase().includes(query.trim().toLowerCase())), [instruments, sector, query])
  const selectedQuote = state?.quotes?.[selected]
  const visibleHistory = history?.symbol === selected ? history : null
  const visibleMinutes = minuteHistory?.symbol === selected ? minuteHistory : null
  const chartHasBars = timeframe === 'daily' ? !!visibleHistory?.bars.length : !!visibleMinutes?.bars.length

  const loadHistory = async () => {
    const symbol = selected
    setHistoryBusy(true); setHistoryError('')
    try { const next = await api<History>(`/api/history?symbol=${encodeURIComponent(symbol)}`); historyCache.current.set(symbol, next); if (selectedRef.current === symbol) setHistory(next) }
    catch (error) { if (selectedRef.current === symbol) setHistoryError((error as Error).message) }
    finally { if (selectedRef.current === symbol) setHistoryBusy(false) }
  }
  const saveKeys = async () => {
    setSettingsBusy(true); setMessage(null)
    try {
      await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ finnhub_key: finnhubKey.trim(), alpha_vantage_key: alphaKey.trim() }) })
      setFinnhubKey(''); setAlphaKey(''); setSettingsOpen(false); setMessage({ text: 'Provider keys saved locally. Connecting to Finnhub…', ok: true }); await refresh()
    } catch (error) { setMessage({ text: (error as Error).message, ok: false }) }
    finally { setSettingsBusy(false) }
  }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark"><Activity size={22} strokeWidth={2.5} /></span><div><strong>US Paper Desk</strong><small>100-stock universe & automatic spot paper lane</small></div></div>
      <div className="header-status"><span className={`live-dot ${state?.feed.status === 'connected' ? 'connected' : ''}`} /><span>{state?.feed.status === 'connected' ? 'Finnhub connected' : state?.feed.finnhub_configured ? `Finnhub ${state.feed.status.replaceAll('_', ' ')}` : 'Finnhub key needed'}</span><span className="header-divider" /><span>{state?.market.open ? 'NYSE OPEN' : 'NYSE CLOSED'}</span><span className="muted time-label">{state?.market.now_et ? new Date(state.market.now_et).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }) : '—'}</span></div>
      <button className="settings-button" onClick={() => setSettingsOpen(true)}><KeyRound size={16} /> Data keys <span className="key-count">{Number(!!state?.feed.finnhub_configured) + Number(!!state?.feed.alpha_vantage_configured)}/2</span></button>
    </header>

    <main className="workspace">
      <div className="page-title"><div><span className="eyebrow">US EQUITIES · PAPER ONLY</span><h1>Trading workspace</h1><p>100 US stocks · four index ETF gauges · one shared ${number(state?.account.initial_capital)} stock paper account</p></div><div className="page-meta"><ShieldCheck size={16}/><span>Blast-inspired spot · $1M paper capital · no broker orders</span></div></div>
      {loadError && <div className="notice error">Backend unavailable: {loadError}</div>}
      {message && <div className={`notice ${message.ok ? 'success' : 'error'}`}><span>{message.text}</span><button aria-label="Dismiss message" onClick={() => setMessage(null)}><X size={15}/></button></div>}
      {state?.feed.error && <div className="notice warn">Finnhub feed: {state.feed.error}</div>}
      {state?.feed.rest_error && state.feed.quotes === 0 && <div className="notice warn">Finnhub quote fallback: {state.feed.rest_error}</div>}

      <section className="metrics-grid">
        <div className="metric-card featured"><div className="metric-head"><span>ACCOUNT EQUITY</span><Wallet size={18}/></div><div className="metric-value">{dollars(state?.account.equity)}</div><div className="metric-foot"><span className={tone(state?.account.return_pct || 0)}>{signed(state?.account.return_pct)}</span><span>since start {state?.account.estimated ? '· estimated marks' : ''}</span></div></div>
        <div className="metric-card"><div className="metric-head"><span>AVAILABLE CASH</span><span className="metric-symbol">$</span></div><div className="metric-value">{dollars(state?.account.cash)}</div><div className="metric-foot">{state ? `${((state.account.cash / state.account.equity) * 100).toFixed(1)}% of equity` : '—'}</div></div>
        <div className="metric-card"><div className="metric-head"><span>MARKET VALUE</span><BarChart3 size={18}/></div><div className="metric-value">{dollars(state?.account.market_value)}</div><div className="metric-foot">{state?.positions.length || 0} open positions</div></div>
        <div className="metric-card"><div className="metric-head"><span>TOTAL P&L</span><span className="metric-symbol">↗</span></div><div className={`metric-value ${tone((state?.account.realized || 0) + (state?.account.unrealized || 0))}`}>{dollars(state ? state.account.realized + state.account.unrealized : undefined)}</div><div className="metric-foot">Realized {dollars(state?.account.realized)} · Open {dollars(state?.account.unrealized)}</div></div>
      </section>

      <section className="index-strip" aria-label="Index ETF gauges"><div className="index-heading">INDEX GAUGES <span>ETF PROXIES</span></div>{state?.indices.map(index => { const quote = state.quotes[index.symbol]; return <div className="index-item" key={index.symbol}><div><strong>{index.name}</strong><small>{index.symbol} · ETF</small></div><div className="index-price"><strong>{dollars(quote?.price)}</strong><small className={tone(quote?.change_pct || 0)}>{quote && quote.previous_close > 0 ? signed(quote.change_pct) : 'Change pending'}</small></div></div> })}</section>

      <section className="main-grid">
        <aside className="panel watchlist"><div className="panel-header"><div><span className="eyebrow">MARKET UNIVERSE</span><h2>Watchlist <span className="count-pill">{instruments.length}</span></h2></div><span className="muted quote-count">{state?.feed.quotes || 0} quotes</span></div><div className="watch-controls"><label className="search-box"><Search size={16}/><input aria-label="Search symbols" placeholder="Search ticker…" value={query} onChange={event => setQuery(event.target.value)}/></label><label className="sector-select"><select aria-label="Sector" value={sector} onChange={event => setSector(event.target.value)}>{sectors.map(value => <option key={value}>{value}</option>)}</select><ChevronDown size={15}/></label></div><div className="watch-head"><span>SYMBOL</span><span>LAST</span><span>CHANGE</span></div><div className="watch-rows">{filtered.map(item => { const quote = state?.quotes[item.symbol]; return <button className={`watch-row ${selected === item.symbol ? 'selected' : ''}`} key={item.symbol} onClick={() => setSelected(item.symbol)}><span><strong>{item.symbol}</strong><small>{item.sector}</small></span><strong>{quote ? dollars(quote.price) : '—'}</strong><span className={`change ${tone(quote?.change_pct || 0)}`}>{quote && quote.previous_close > 0 ? signed(quote.change_pct) : '—'}</span></button> })}{filtered.length === 0 && <div className="empty-row">No symbols match.</div>}</div></aside>

        <div className="center-column"><div className="panel detail-panel">
          <div className="detail-heading"><div><span className="eyebrow">STOCK DETAIL</span><div className="symbol-line"><h2>{selected}</h2><span className="instrument-badge">US STOCK</span></div><span className="muted">{instruments.find(item => item.symbol === selected)?.sector || '—'}</span></div><div className="selected-price"><strong>{dollars(selectedQuote?.price)}</strong><span className={tone(selectedQuote?.change_pct || 0)}>{selectedQuote && selectedQuote.previous_close > 0 ? selectedQuote.change_pct >= 0 ? <ArrowUpRight size={15}/> : <ArrowDownRight size={15}/> : null}{selectedQuote && selectedQuote.previous_close > 0 ? signed(selectedQuote.change_pct) : selectedQuote ? 'Change pending' : 'No live quote'}</span></div></div>
          <div className="quote-meta"><span><Clock3 size={13}/> {selectedQuote ? `${selectedQuote.source} · ${since(selectedQuote.trade_at)}` : 'Waiting for Finnhub quote'}</span><span>Previous close {dollars(selectedQuote?.previous_close && selectedQuote.previous_close > 0 ? selectedQuote.previous_close : undefined)}</span></div>
          <div className="chart-header"><div><h3>Price and volume</h3><span>{timeframe === 'daily' ? 'Alpha Vantage · 100 daily candles' : 'Observed Finnhub trades · closed 1m bars'}</span></div><div className="chart-actions"><div className="chart-tabs" role="group" aria-label="Chart timeframe"><button className={timeframe === 'daily' ? 'active' : ''} onClick={() => setTimeframe('daily')}>Daily {visibleHistory?.bars.length ? `(${visibleHistory.bars.length})` : ''}</button><button className={timeframe === 'minute' ? 'active' : ''} onClick={() => setTimeframe('minute')}>1m {visibleMinutes?.bars.length ? `(${visibleMinutes.bars.length})` : ''}</button></div><button className="secondary-button" disabled={historyBusy || !state?.feed.alpha_vantage_configured} onClick={() => void loadHistory()}>{historyBusy ? <LoaderCircle size={14} className="spin"/> : <BarChart3 size={14}/>} Refresh daily</button></div></div>
          {chartHasBars ? <><PriceChart daily={visibleHistory} minute={visibleMinutes} timeframe={timeframe} symbol={selected}/><div className="chart-caption">{timeframe === 'daily' ? `${visibleHistory!.source} · through ${visibleHistory!.bars.at(-1)?.day} · fetched ${since(visibleHistory!.fetched_at)} · live quote shown above` : `${visibleMinutes!.source} · ${visibleMinutes!.bars.length} closed bars · last ${new Date(visibleMinutes!.bars.at(-1)!.at).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })}`}</div></> : <div className="chart-placeholder"><BarChart3 size={32}/><strong>{timeframe === 'daily' ? historyError || (historyBusy ? 'Loading daily candles…' : !state?.feed.alpha_vantage_configured ? 'Add Alpha Vantage key for daily candles' : 'Daily history is loading') : minuteError || 'Collecting observed one-minute bars'}</strong><span>{timeframe === 'daily' ? 'The selected stock loads automatically and is cached to protect the provider quota.' : 'Only closed bars built from actual Finnhub WebSocket trades appear here.'}</span></div>}
        </div>
        <div className="panel positions-panel"><div className="section-header"><div><span className="eyebrow">SPOT PAPER BOOK</span><h2>Stock positions <span className="count-pill">{state?.positions.length || 0}</span></h2></div></div>{state?.positions.length ? <div className="table-wrap"><table><thead><tr><th>SYMBOL</th><th>SHARES</th><th>ENTRY</th><th>MARK</th><th>OPEN P&L</th></tr></thead><tbody>{state.positions.map(position => <tr key={position.symbol}><td>{position.symbol}</td><td>{number(position.quantity)}</td><td>{dollars(position.average_cost)}</td><td>{dollars(position.market_price)}</td><td className={tone(position.unrealized)}>{dollars(position.unrealized)}</td></tr>)}</tbody></table></div> : <div className="small-empty">No stock positions. The scanner needs 120 real closed minute bars per symbol before a signal can qualify.</div>}</div></div>

        <aside className="right-column"><BlastPanel spot={state?.spot} busy={automationBusy} finnhubConnected={state?.feed.status === 'connected'} onToggle={() => void toggleAutomation()}/><div className="panel equity-panel"><div className="section-header"><div><span className="eyebrow">ACCOUNT</span><h2>Equity curve</h2></div></div>{(state?.equity_history.length || 0) >= 2 ? <EquityChart points={state!.equity_history}/> : <div className="small-empty">Equity history appears after paper activity.</div>}</div><div className="panel activity-panel"><div className="section-header"><div><span className="eyebrow">JOURNAL</span><h2>Recent fills</h2></div></div>{state?.fills.length ? <div className="fills-list">{state.fills.slice(0, 8).map(fill => <div className="fill-row" key={fill.id}><span className={`fill-side ${fill.side.toLowerCase()}`}>{fill.side}</span><span><strong>{fill.symbol} × {fill.quantity}</strong><small>{new Date(fill.at).toLocaleString()}</small></span><strong>{dollars(fill.price)}</strong></div>)}</div> : <div className="small-empty">No paper fills yet.</div>}</div></aside>
      </section>
      <footer>Quotes: Finnhub · Daily charts: Alpha Vantage · Index cards use ETF proxies, not official index levels · Paper account only</footer>
    </main>

    {settingsOpen && <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setSettingsOpen(false) }}><div className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="modal-head"><div><span className="eyebrow">MARKET DATA</span><h2 id="settings-title">Provider keys</h2></div><button className="icon-button" aria-label="Close" onClick={() => setSettingsOpen(false)}><X size={20}/></button></div><p>Keys stay in this Docker service's local data volume. The app never displays a saved key.</p><label>Finnhub API key <span className="provider-state">{state?.feed.finnhub_configured ? <><Check size={13}/> Saved</> : 'Required for quotes'}</span><input type="password" autoComplete="off" placeholder={state?.feed.finnhub_configured ? 'Leave blank to keep saved key' : 'Paste Finnhub key'} value={finnhubKey} onChange={event => setFinnhubKey(event.target.value)}/></label><label>Alpha Vantage API key <span className="provider-state">{state?.feed.alpha_vantage_configured ? <><Check size={13}/> Saved</> : 'For daily charts'}</span><input type="password" autoComplete="off" placeholder={state?.feed.alpha_vantage_configured ? 'Leave blank to keep saved key' : 'Paste Alpha Vantage key'} value={alphaKey} onChange={event => setAlphaKey(event.target.value)}/></label><div className="modal-note">Finnhub provides quotes and paper fill prices. Alpha Vantage charts load only when you press “Load chart.” Free Alpha Vantage keys have a daily request limit.</div><button className="save-button" disabled={settingsBusy || (!finnhubKey.trim() && !alphaKey.trim())} onClick={() => void saveKeys()}>{settingsBusy ? 'Saving…' : 'Save keys'}</button></div></div>}
  </div>
}
