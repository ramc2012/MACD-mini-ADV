# How long does sector relative performance last? (daily, 17 Oct 2026)

Data: Fyers daily closes Jan 2019 → 16 Oct 2026 (1,933 sessions), 13 sector indices vs NIFTY 50; 210 F&O stocks for
comparison. Code `persistence.py`, output `report.txt`, chart `persistence.png`. Before costs; overlapping samples use
Newey-West t-stats.

## 1. Decay: rank every day on recent relative return, hold top 3 vs bottom 3
- Short look-backs (1–10 days): the spread builds for **10–15 sessions** (+0.3 to +0.45%) and is significant there,
  then fades back toward zero by 30–60 sessions. Peak day 13–22. That is the "undercurrent": real, small, about 3 weeks.
- 60-day leaders keep drifting ahead for 60 sessions (+0.9%) but never reach significance.
- Regime-dependent: 20-day ranking earned +1.3% (20 sessions later) in 2023–24, −0.3% in 2021–22, ~0 in 2025–26.
- Stocks behave differently: the 20/60-day spread keeps compounding to +1.9 to +2.3% over 60 sessions (significant
  from 10 sessions on), i.e. slower, longer momentum (part survivorship).

## 2. Month-start winners (the user's hypothesis)
Top 3 vs bottom 3 by relative return over the month's first 3 sessions, 93 months:
| sessions after | +1 | +3 | +5 | +10 | +15 | +20 | +40 |
|---|---|---|---|---|---|---|---|
| month-start formation | +0.11% | **+0.52% (t 3.1)** | +0.48% (t 2.3) | +0.57% (t 1.8) | +0.62% | +0.29% | +0.75% |
| same rule formed any day | +0.05% | +0.10% | +0.14% | +0.26% | +0.34% | +0.29% | +0.15% |

Month-start leaders out-run the laggards about twice as hard as the same signal on a random day, peak around session
13 (+0.77%), and give most of it back by session 20 (month end). So: they do not win the whole month, but there is an
early-month undercurrent of 2–3 weeks, strongest in the first 3–5 sessions after formation. With 93 months and several
horizons tested this is **promising, not proven**.

## 3. Daily RRG quadrants
- Quadrants are short-lived on daily bars: median 3 (Leading, Lagging) to 5 (Weakening, Improving) sessions.
- Rotation is clockwise as the textbook says: Leading → Weakening 89%, Lagging → Improving 90%; from Weakening and
  Improving it is a coin toss (≈50/50) whether the name re-accelerates or rolls over.
- Forward relative return by quadrant is tiny: right side (Leading/Weakening) +0.13 to +0.25% over 20 sessions, left
  side ≈ 0 to −0.09%. The x-axis (RS-Ratio, the trend) carries the little information there is; the y-axis hook
  (momentum turning) adds nothing on daily bars.

## 4. Leadership runs
A sector in the top 3 by 20-day relative return stays there a median of 2 sessions (mean 5.8, 90th percentile 18);
only 9% of runs last 20+ sessions.

## Use
- Read sector strength on 2–4 week windows; treat a 1–5 day pop with suspicion unless the 20/60-day rings agree.
- Month-start leaders: lean with them for ~2–3 weeks, not to month end.
- Do not trade daily RRG quadrant changes by themselves.
These inform the app's Sector wheel (Market → Sector wheel).
