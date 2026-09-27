import { Activity, AlertTriangle, Pause, Play } from 'lucide-react'

export type SpotStatus = { mode: string; auto_enabled: boolean; scanner_symbols: number; universe_symbols: number; symbols_with_bars: number; ready_symbols: number; fresh_breadth_cohort: number; breadth_positive_pct: number; warmup_bars: number; min_bars: number; last_closed_bar?: string; last_decision?: string; last_fill?: string; dropped_trades: number; data_note: string }

export function BlastPanel({ spot, finnhubConnected, busy, onToggle }: { spot?: SpotStatus; finnhubConnected: boolean; busy: boolean; onToggle: () => void }) {
  const ready = !!spot && finnhubConnected && spot.auto_enabled && spot.ready_symbols >= 20 && spot.fresh_breadth_cohort >= 20 && spot.dropped_trades === 0
  return <div className="panel blast-panel">
    <div className="panel-header"><div><span className="eyebrow">AUTOMATIC STOCK PAPER TRADING</span><h2>Blast-inspired spot</h2></div><span className="paper-badge">PAPER</span></div>
    <div className={`blast-state ${ready ? 'ready' : 'warn'}`}>
      {ready ? <Activity size={18}/> : <AlertTriangle size={18}/>}
      <div><strong>{!spot?.auto_enabled ? 'Automation paused' : ready ? 'Scanner ready' : 'Collecting real minute bars'}</strong><span>{ready ? 'Entries require a completed one-minute signal, breadth, liquidity and a fresh trade price.' : 'No historical minute access with the saved keys. The scanner must build its own bars from live Finnhub trades.'}</span></div>
    </div>
    <div className="blast-steps">
      <div><span>Stock universe</span><strong>{spot?.universe_symbols ?? 100} watchlist</strong></div>
      <div><span>Active WebSocket scanner</span><strong>{finnhubConnected ? `${spot?.scanner_symbols ?? 0} stocks` : 'Disconnected'}</strong></div>
      <div><span>Bars collected</span><strong>{spot?.symbols_with_bars ?? 0} symbols</strong></div>
      <div><span>Longest warmup</span><strong>{spot?.warmup_bars ?? 0} / {spot?.min_bars ?? 120} bars</strong></div>
      <div><span>Ready symbols / breadth cohort</span><strong>{spot?.ready_symbols ?? 0} / {spot?.fresh_breadth_cohort ?? 0}</strong></div>
      <div><span>Positive MACD breadth</span><strong>{spot?.fresh_breadth_cohort ? `${spot.breadth_positive_pct.toFixed(0)}%` : '—'}</strong></div>
      <div><span>Automatic paper orders</span><strong className={spot?.auto_enabled ? 'positive' : 'negative'}>{spot?.auto_enabled ? 'Enabled' : 'Paused'}</strong></div>
    </div>
    {spot?.dropped_trades ? <p className="blast-reason">{spot.dropped_trades} live trades were dropped. New entries are blocked until the feed restarts cleanly.</p> : null}
    {spot?.last_decision ? <p className="blast-decision">Last signal: {spot.last_decision}</p> : null}
    {spot?.last_fill ? <p className="blast-decision">Last paper fill: {spot.last_fill}</p> : null}
    <div className="blast-rules"><strong>Spot adaptation</strong><span>Closed 1m stock MACD(12,26,9) signal cross · at least 120 prior bars · 50% positive MACD breadth across 20 fresh stocks · price at least 25% below its prior 750-bar high · order at most 2% of its last-minute volume · 10 positions / 10% equity each · −50% hard stop · 40% trail after +30%. The option premium/spot and option-side breadth filters have no stock equivalent, so this is an unvalidated variant of the original options strategy.</span></div>
    <button className="secondary-button blast-check" onClick={onToggle} disabled={busy || !spot}>{spot?.auto_enabled ? <Pause size={14}/> : <Play size={14}/>} {busy ? 'Updating…' : spot?.auto_enabled ? 'Pause automation' : 'Resume automation'}</button>
  </div>
}
