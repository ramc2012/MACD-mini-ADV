"""Pure summary calculations copied from Nomad CBE. No file I/O, marking or execution methods."""
from __future__ import annotations
import math
from datetime import datetime, timezone, timedelta
from typing import Any
CBE_INITIAL_CAPITAL: float = 1000000.0
DEFAULT_POSITION_NOTIONAL_CAP: float = 100000.0
MIN_HOLD_TRADING_DAYS: int = 5
HEDGE_MAX_GROSS_EXPOSURE_RATIO: float = 1.0
HEDGE_MAX_NET_EXPOSURE_RATIO: float = 0.4
HEDGE_MAX_SINGLE_NAME_RATIO: float = 0.1
HEDGE_MAX_SECTOR_EXPOSURE_RATIO: float = 0.3
CBE_HARD_STOP_LOSS_PCT: float = 0.05
CBE_SLIPPAGE_BPS: float = 5.0
CBE_TRANSACTION_COST_BPS_PER_LEG: float = 10.0
CBE_MIN_POSITION_FILL_RATIO: float = 0.5
HEDGE_REBALANCE_DRIFT_RATIO: float = 0.05

def _coerce_float(value: Any, default: float=0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out

def _position_notional(position: dict[str, Any]) -> float:
    mark = _coerce_float(position.get('latest_close'), _coerce_float(position.get('entry_price')))
    qty = _coerce_float(position.get('quantity'))
    if mark > 0 and qty > 0:
        return abs(mark * qty)
    return abs(_coerce_float(position.get('notional')))

class RetainedSummary:
    initial_capital = CBE_INITIAL_CAPITAL
    hard_stop_loss_pct = CBE_HARD_STOP_LOSS_PCT

    def _portfolio_exposure(self, open_positions: list[dict[str, Any]]) -> dict[str, Any]:
        long_exposure = 0.0
        short_exposure = 0.0
        sectors: dict[str, dict[str, Any]] = {}
        names: list[float] = []
        for pos in open_positions:
            notional = _position_notional(pos)
            if notional <= 0:
                continue
            direction = str(pos.get('direction') or '').lower()
            sector = str(pos.get('sector_code') or 'unclassified').strip() or 'unclassified'
            sector_row = sectors.setdefault(sector, {'sector': sector, 'long': 0.0, 'short': 0.0, 'gross': 0.0, 'names': 0})
            if direction == 'short':
                short_exposure += notional
                sector_row['short'] += notional
            else:
                long_exposure += notional
                sector_row['long'] += notional
            sector_row['gross'] += notional
            sector_row['names'] += 1
            names.append(notional)
        gross = long_exposure + short_exposure
        net = long_exposure - short_exposure
        names.sort(reverse=True)
        return {'long': round(long_exposure, 2), 'short': round(short_exposure, 2), 'gross': round(gross, 2), 'net': round(net, 2), 'largest': round(names[0], 2) if names else 0.0, 'top3': round(sum(names[:3]), 2), 'sectors': sectors}

    def _summary(self, open_positions: list[dict[str, Any]], closed_positions: list[dict[str, Any]], *, peak_equity: Any=None, max_drawdown_seen: Any=None) -> dict[str, Any]:
        realized = round(sum((float(r.get('realized_pnl') or 0.0) for r in closed_positions)), 2)
        unrealized = round(sum((float(p.get('unrealized_pnl') or 0.0) for p in open_positions)), 2)
        gross_realized = round(sum((_coerce_float(r.get('gross_realized_pnl'), _coerce_float(r.get('realized_pnl'))) for r in closed_positions)), 2)
        gross_unrealized = round(sum((_coerce_float(p.get('gross_unrealized_pnl'), _coerce_float(p.get('unrealized_pnl'))) for p in open_positions)), 2)
        closed_costs = round(sum((_coerce_float(r.get('transaction_costs')) for r in closed_positions)), 2)
        estimated_open_costs = round(sum((_coerce_float(p.get('estimated_transaction_costs')) for p in open_positions)), 2)
        reserved_margin = round(sum((float(p.get('entry_price') or 0.0) * float(p.get('quantity') or 0) for p in open_positions)), 2)
        initial_capital = self.initial_capital
        total_equity = round(initial_capital + realized + unrealized, 2)
        available_capital = round(initial_capital + realized - reserved_margin, 2)
        total_return_pct = round((total_equity - initial_capital) / initial_capital * 100.0, 4) if initial_capital else 0.0
        recorded_peak = max(initial_capital, _coerce_float(peak_equity, initial_capital), total_equity)
        current_drawdown = max(0.0, (recorded_peak - total_equity) / recorded_peak) if recorded_peak > 0 else 0.0
        exposure = self._portfolio_exposure(open_positions)
        equity_base = total_equity if total_equity > 0 else initial_capital
        gross_ratio = exposure['gross'] / equity_base if equity_base else 0.0
        net_ratio = exposure['net'] / equity_base if equity_base else 0.0
        largest_ratio = exposure['largest'] / equity_base if equity_base else 0.0
        concentration_top3_ratio = exposure['top3'] / equity_base if equity_base else 0.0
        long_count = sum((1 for p in open_positions if str(p.get('direction') or '').lower() == 'long'))
        short_count = sum((1 for p in open_positions if str(p.get('direction') or '').lower() == 'short'))
        sector_exposures: list[dict[str, Any]] = []
        for sector, row in exposure['sectors'].items():
            gross = float(row.get('gross') or 0.0)
            long_val = float(row.get('long') or 0.0)
            short_val = float(row.get('short') or 0.0)
            sector_exposures.append({'sector': sector, 'long_exposure': round(long_val, 2), 'short_exposure': round(short_val, 2), 'gross_exposure': round(gross, 2), 'net_exposure': round(long_val - short_val, 2), 'gross_exposure_ratio': round(gross / equity_base if equity_base else 0.0, 4), 'names': int(row.get('names') or 0)})
        sector_exposures.sort(key=lambda r: float(r.get('gross_exposure') or 0.0), reverse=True)
        top_sector_ratio = float(sector_exposures[0]['gross_exposure_ratio']) if sector_exposures else 0.0
        risk_flags: list[str] = []
        drift = 1.0 + HEDGE_REBALANCE_DRIFT_RATIO
        effective_net_ratio = 1.0 if any((p.get('selection_policy') == 'daily-stock-direction-v1-20261017' for p in open_positions)) else HEDGE_MAX_NET_EXPOSURE_RATIO
        if gross_ratio > HEDGE_MAX_GROSS_EXPOSURE_RATIO * drift:
            risk_flags.append('gross_exposure_over_mandate')
        if abs(net_ratio) > effective_net_ratio * drift:
            risk_flags.append('net_exposure_over_mandate')
        if largest_ratio > HEDGE_MAX_SINGLE_NAME_RATIO * drift:
            risk_flags.append('single_name_over_mandate')
        if top_sector_ratio > HEDGE_MAX_SECTOR_EXPOSURE_RATIO * drift:
            risk_flags.append('sector_concentration_over_mandate')
        if not risk_flags and open_positions:
            risk_flags.append('inside_hedge_mandate')
        if abs(net_ratio) < 0.05:
            book_bias = 'balanced'
        elif net_ratio > 0:
            book_bias = 'net_long'
        else:
            book_bias = 'net_short'
        closed_sorted = sorted(closed_positions, key=lambda r: str(r.get('closed_at') or r.get('updated_at') or ''))
        running_equity = initial_capital
        peak = initial_capital
        max_dd = max(_coerce_float(max_drawdown_seen), current_drawdown)
        trade_returns_pct: list[float] = []
        wins = 0
        losses = 0
        for row in closed_sorted:
            pnl = float(row.get('realized_pnl') or 0.0)
            pre_equity = running_equity if running_equity > 0 else initial_capital
            running_equity = max(0.0, running_equity + pnl)
            if running_equity > peak:
                peak = running_equity
            if peak > 0:
                dd = (peak - running_equity) / peak
                max_dd = max(max_dd, dd)
            if pre_equity > 0:
                trade_returns_pct.append(pnl / pre_equity * 100.0)
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
        sharpe = 0.0
        if len(trade_returns_pct) >= 2:
            mean = sum(trade_returns_pct) / len(trade_returns_pct)
            var = sum(((r - mean) ** 2 for r in trade_returns_pct)) / max(len(trade_returns_pct) - 1, 1)
            stdev = var ** 0.5
            if stdev > 0:
                sharpe = round(mean / stdev, 4)
        win_rate = wins / (wins + losses) if wins + losses else 0.0
        return {'open_positions': len(open_positions), 'closed_positions': len(closed_positions), 'realized_pnl': realized, 'unrealized_pnl': unrealized, 'total_pnl': round(realized + unrealized, 2), 'gross_realized_pnl': gross_realized, 'gross_unrealized_pnl': gross_unrealized, 'transaction_costs': closed_costs, 'estimated_open_costs': estimated_open_costs, 'performance_quality': 'legacy_quote_provenance_unverified', 'performance_note': 'Legacy history includes known July tick-price conflicts; preserve the ledger and evaluate new verified-quote entries separately.', 'prospective_performance': {'cohort': 'cbe_causal_quotes_v2', 'closed_count': sum((p.get('performance_cohort') == 'cbe_causal_quotes_v2' for p in closed_positions)), 'open_count': sum((p.get('performance_cohort') == 'cbe_causal_quotes_v2' for p in open_positions)), 'realized_pnl': round(sum((_coerce_float(p.get('realized_pnl')) for p in closed_positions if p.get('performance_cohort') == 'cbe_causal_quotes_v2')), 2)}, 'selection_performance': {'cohort': 'cbe_daily_stock_v3', 'closed_count': sum((p.get('performance_cohort') == 'cbe_daily_stock_v3' for p in closed_positions)), 'open_count': sum((p.get('performance_cohort') == 'cbe_daily_stock_v3' for p in open_positions)), 'realized_pnl': round(sum((_coerce_float(p.get('realized_pnl')) for p in closed_positions if p.get('performance_cohort') == 'cbe_daily_stock_v3')), 2)}, 'initial_capital': initial_capital, 'available_capital': available_capital, 'reserved_margin': reserved_margin, 'total_equity': total_equity, 'total_return_pct': total_return_pct, 'long_positions': long_count, 'short_positions': short_count, 'long_exposure': exposure['long'], 'short_exposure': exposure['short'], 'gross_exposure': exposure['gross'], 'net_exposure': exposure['net'], 'gross_exposure_ratio': round(gross_ratio, 4), 'net_exposure_ratio': round(net_ratio, 4), 'largest_position_exposure': exposure['largest'], 'largest_position_ratio': round(largest_ratio, 4), 'concentration_top3_ratio': round(concentration_top3_ratio, 4), 'risk_budget_usage_ratio': round(min(1.0, gross_ratio / HEDGE_MAX_GROSS_EXPOSURE_RATIO), 4), 'book_bias': book_bias, 'sector_exposures': sector_exposures, 'risk_flags': risk_flags, 'mandate': {'strategy': 'daily_long_only_new_entries_legacy_holdings_preserved', 'selection_policy': 'daily-stock-direction-v1-20261017', 'new_entries_long_only': True, 'new_entry_max_net_exposure_ratio': 1.0, 'max_gross_exposure_ratio': HEDGE_MAX_GROSS_EXPOSURE_RATIO, 'max_net_exposure_ratio': effective_net_ratio, 'max_single_name_ratio': HEDGE_MAX_SINGLE_NAME_RATIO, 'max_sector_exposure_ratio': HEDGE_MAX_SECTOR_EXPOSURE_RATIO, 'rebalance': 'daily for new selections; legacy holdings retain recorded terms', 'min_hold_trading_days': MIN_HOLD_TRADING_DAYS, 'new_selection_min_hold_trading_days': 1, 'hard_stop_loss_pct': self.hard_stop_loss_pct, 'rebalance_drift_ratio': HEDGE_REBALANCE_DRIFT_RATIO, 'execution_model': 'cash_longs_and_single_stock_futures_proxy_shorts'}, 'peak_equity': round(recorded_peak, 2), 'current_drawdown': round(current_drawdown, 4), 'max_drawdown': round(max_dd, 4), 'sharpe_ratio': sharpe, 'total_trades': wins + losses, 'win_rate': round(win_rate, 4)}

def summarize(data):
    return RetainedSummary()._summary(data.get("open_positions") or [], data.get("closed_positions") or [], peak_equity=data.get("peak_equity"), max_drawdown_seen=data.get("max_drawdown"))
