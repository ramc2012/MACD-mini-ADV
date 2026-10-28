"""Bounded local allocation policy, without inference, network access or orders.

Priority is an explanatory heuristic, not a probability or expected return. The
old exported ranker is one input, not proven edge. No score veto: constraints
and existing size limits remain with SmartLane/ExecutionManager.
"""
from __future__ import annotations

import math

VERSION = 'smart-local-allocation-v1'
WEIGHTS = {'research_rank': .25, 'premium_momentum': .25, 'side_breadth': .25, 'spot_alignment': .15,
           'diversification': .10}


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def bounded(value):
    return min(1., max(0., value))


def alignment(value, scale):
    value = number(value)
    return .5 if value is None else .5 + .5 * math.tanh(value / scale)


def evaluate(*, research_percentile, option_type, breadth=None, live=None, exposure_fraction=0.):
    live = live or {}
    own = other = None
    if breadth is not None and breadth.ce_eligible >= 20 and breadth.pe_eligible >= 20:
        ce, pe = breadth.ce_above / breadth.ce_eligible, breadth.pe_above / breadth.pe_eligible
        own, other = (ce, pe) if option_type == 'CE' else (pe, ce)
    rank = number(research_percentile)
    components = {
        'research_rank': bounded(rank) if rank is not None else .5,
        'premium_momentum': alignment(live.get('macd_pct'), .005),
        'side_breadth': .5 if own is None else bounded(.5 + (own - other) / 2),
        # Live inputs are already signed by CE/PE. A PE is not a short premium.
        'spot_alignment': sum(alignment(live.get(k), .005) for k in ('u_ret_open', 'u_vwap', 'nifty_ret_prev')) / 3,
        'diversification': 1 - bounded(max(0., number(exposure_fraction) or 0.) / .2),
    }
    priority = sum(WEIGHTS[k] * v for k, v in components.items())
    return {'policy': VERSION, 'priority': round(priority, 6), 'components': components,
            'weights': WEIGHTS, 'live_inputs': live, 'own_breadth': own, 'other_breadth': other,
            'flow_use': 'diagnostic_only; historical directional edge unproven',
            'basis': 'heuristic allocation priority; not calibrated profit probability', 'qwen_required': False}
