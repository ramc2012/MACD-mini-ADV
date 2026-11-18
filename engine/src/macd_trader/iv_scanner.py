"""IV scanner (owner, 18 Nov 2026): every F&O underlying's at-the-money implied volatility against its own history.

Source: the option chains the engine already stores (chain_snapshots in historical.sqlite3: index chains about once a
minute since 3 Sep 2026, F&O stock chains every 15-minute sweep since 26 Oct 2026). Nothing is downloaded.

Per underlying and session, the session's LAST stored snapshot gives one point:
  iv       30-day constant-maturity ATM IV: per stored expiry, the mean call/put Black-Scholes IV (option_greeks,
           r = 6.5%) at the strike nearest spot, on the two-sided quote mid (else a last price traded that session; call and put IV within
           PARITY_POINTS of each other, else the next strike; a last price can be hours old on a thin contract); total variance is
           interpolated between the two expiries around 30 days (stocks), else the expiry nearest 30 days is used
           (index chains store the front weekly only: their tenor is shown). Expiry-day contracts are skipped, so a
           stock's IV does not jump onto a dying front month in its expiry week.
  straddle call + put at that strike, as % of spot (the market's expected move to that expiry)
Today's point is the latest snapshot so far. History points are kept in runtime/iv_history.json (a past session's
answer never changes) so a request recomputes only today.

IV rank = where today's IV sits between the history's low and high (0-100); IV percentile = share of earlier sessions
with lower IV. HV20 = 20-session close-to-close volatility of the spot (daily_closes). The history is short (stocks
about 18 sessions on 18 Nov 2026), so rank and percentile are labelled with their session count and read as rough.
Measurement only: no lane reads it.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .option_greeks import implied_vol
from .sectors import sector_of
from .universe import INDEX_SPOTS

IST = ZoneInfo("Asia/Kolkata")
MIN_DAYS = 5
TENOR_DAYS = 30
IV_FLOOR, IV_CEILING = 0.03, 3.0
PARITY_POINTS = 12.0
MIN_SESSIONS = 5
EXPIRY_CLOSE = time(15, 30)
CACHE_VERSION = 4


def spot_symbol(underlying: str) -> str:
    return INDEX_SPOTS.get(underlying) or f"NSE:{underlying}-EQ"


def _years(expiry: str, ts: int) -> float:
    close = datetime.combine(date.fromisoformat(expiry[:10]), EXPIRY_CLOSE, IST)
    return max((close.timestamp() - ts) / (365.0 * 86400), 0.5 / 365)


def _quoted(ltp, bid, ask, volume) -> float | None:
    """The quote mid when two-sided and not absurdly wide; else the last price only if the contract traded in the
    session (a last price can be days old on a thin contract)."""
    if bid and ask and 0 < bid <= ask and (ask - bid) / ((ask + bid) / 2) <= 0.25:
        return (bid + ask) / 2
    return float(ltp) if ltp and ltp > 0 and volume and volume > 0 else None


def _expiry_atm(rows: list[tuple], expiry: str, spot: float, ts: int) -> dict | None:
    """ATM IV of one expiry: the strike nearest spot whose call and put both have a sane two-sided quote, and whose
    call and put IVs agree within PARITY_POINTS (a wider gap is a stale or broken quote, e.g. next month's thin book)."""
    legs: dict[float, dict[str, float]] = {}
    for r in rows:
        if r[0][:10] != expiry:
            continue
        price = _quoted(r[3], r[4], r[5], r[7] if len(r) > 7 else 0)
        if price:
            legs.setdefault(float(r[1]), {})[r[2]] = price
    years = _years(expiry, ts)
    for strike in sorted((k for k, v in legs.items() if "CE" in v and "PE" in v), key=lambda k: abs(k - spot))[:3]:
        ce_iv = implied_vol(legs[strike]["CE"], spot, strike, years, True)
        pe_iv = implied_vol(legs[strike]["PE"], spot, strike, years, False)
        if not (ce_iv and pe_iv and IV_FLOOR <= ce_iv <= IV_CEILING and IV_FLOOR <= pe_iv <= IV_CEILING):
            continue
        if abs(ce_iv - pe_iv) * 100 > PARITY_POINTS:
            continue
        return {"expiry": expiry, "years": years, "strike": strike, "iv": (ce_iv + pe_iv) / 2, "ce_iv": ce_iv,
                "pe_iv": pe_iv, "straddle": legs[strike]["CE"] + legs[strike]["PE"]}
    return None


def atm_point(rows: list[tuple], ts: int) -> dict | None:
    """rows: (expiry, strike, option_type, ltp, bid, ask, spot, volume) of one snapshot.

    The point is a constant-maturity TENOR_DAYS IV: total variance interpolated between the stored expiries that
    bracket it (stocks store the front two months), else the stored expiry nearest the tenor (index chains keep the
    front weekly only). Expiry-day contracts are never used. The shown strike, straddle and leg IVs belong to the
    expiry nearest the tenor that is at least MIN_DAYS out."""
    spots = [r[6] for r in rows if r[6] and r[6] > 0]
    if not spots:
        return None
    spot = statistics.median(spots)
    day = datetime.fromtimestamp(ts, IST).date()
    points = [p for e in sorted({r[0][:10] for r in rows}) if (date.fromisoformat(e) - day).days >= 1
              for p in [_expiry_atm(rows, e, spot, ts)] if p]
    if not points:
        return None
    target = TENOR_DAYS / 365.0
    below = [p for p in points if p["years"] <= target]
    above = [p for p in points if p["years"] > target]
    if below and above:
        a, b = below[-1], above[0]
        wa, wb = a["iv"] ** 2 * a["years"], b["iv"] ** 2 * b["years"]
        var = wa + (wb - wa) * (target - a["years"]) / (b["years"] - a["years"])
        iv, tenor = math.sqrt(max(var, 0) / target), f"{TENOR_DAYS}d"
    else:
        near = min(points, key=lambda p: abs(p["years"] - target))
        iv, tenor = near["iv"], f"{round(near['years'] * 365)}d"
    usable = [p for p in points if (date.fromisoformat(p["expiry"]) - day).days >= MIN_DAYS] or points
    shown = min(usable, key=lambda p: abs(p["years"] - target))
    pct = lambda v: round(v * 100, 2) if v else None  # noqa: E731
    return {"ts": ts, "expiry": shown["expiry"], "dte": (date.fromisoformat(shown["expiry"]) - day).days,
            "spot": round(spot, 2), "strike": shown["strike"], "iv": round(iv * 100, 2), "tenor": tenor,
            "expiry_iv": pct(shown["iv"]), "ce_iv": pct(shown["ce_iv"]), "pe_iv": pct(shown["pe_iv"]),
            "straddle": round(shown["straddle"], 2), "straddle_pct": round(shown["straddle"] / spot * 100, 2)}


def _bounds(day: str) -> tuple[int, int]:
    d = date.fromisoformat(day)
    return (int(datetime.combine(d, time(9, 0), IST).timestamp()),
            int(datetime.combine(d, time(15, 45), IST).timestamp()))


def day_point(db: sqlite3.Connection, underlying: str, day: str) -> dict | None:
    lo, hi = _bounds(day)
    row = db.execute("SELECT MAX(ts) FROM chain_snapshots WHERE underlying = ? AND ts BETWEEN ? AND ?",
                     (underlying, lo, hi)).fetchone()
    if not row or row[0] is None:
        return None
    ts = int(row[0])
    rows = db.execute("""SELECT expiry, strike, option_type, ltp, bid, ask, spot, volume FROM chain_snapshots
                         WHERE underlying = ? AND ts = ?""", (underlying, ts)).fetchall()
    return atm_point(rows, ts)


def hv20(closes: list[float]) -> float | None:
    """Annualised close-to-close volatility (%) over the last 20 returns."""
    closes = [c for c in closes if c and c > 0][-21:]
    if len(closes) < 11:
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    return round(statistics.stdev(rets) * math.sqrt(252) * 100, 2)


def rank(current: float, history: list[float]) -> tuple[float | None, float | None]:
    """(IV rank, IV percentile) of ``current`` against ``history`` (earlier sessions only)."""
    if len(history) < MIN_SESSIONS - 1:
        return None, None
    low, high = min(history + [current]), max(history + [current])
    iv_rank = round((current - low) / (high - low) * 100, 1) if high > low else 50.0
    pctl = round(sum(1 for v in history if v < current) / len(history) * 100, 1)
    return iv_rank, pctl


class IvScanner:
    def __init__(self, research_path: str, cache_path: str | Path):
        self.research_path = research_path
        self.cache_path = Path(cache_path)
        self.history: dict[str, dict[str, dict]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.cache_path.read_text())
            if data.get("version") == CACHE_VERSION:
                self.history = data.get("history") or {}
        except (OSError, ValueError):
            self.history = {}

    def _save(self) -> None:
        try:
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": CACHE_VERSION, "history": self.history}, separators=(",", ":")))
            tmp.replace(self.cache_path)
        except OSError:
            pass

    def scan(self, now: datetime | None = None) -> dict:
        now = (now or datetime.now(IST)).astimezone(IST)
        today = now.date().isoformat()
        self._load()
        added = 0
        with sqlite3.connect(f"file:{self.research_path}?mode=ro", uri=True, timeout=30) as db:
            pairs = db.execute("SELECT underlying, day FROM chain_days ORDER BY underlying, day").fetchall()
            latest_day: dict[str, str] = {}
            for underlying, day in pairs:
                latest_day[underlying] = max(day, latest_day.get(underlying, ""))
                past = self.history.setdefault(underlying, {})
                if day < today and day not in past:
                    past[day] = day_point(db, underlying, day) or {}
                    added += 1
            current = {u: day_point(db, u, d) for u, d in latest_day.items()}
            closes: dict[str, list[float]] = {}
            since = (now.date() - timedelta(days=45)).isoformat()
            for u in latest_day:
                closes[u] = [r[0] for r in db.execute(
                    "SELECT close FROM daily_closes WHERE symbol = ? AND day >= ? AND day < ? ORDER BY day",
                    (spot_symbol(u), since, today))]
        if added:
            self._save()
        rows = []
        for u, point in current.items():
            if not point:
                continue
            day = latest_day[u]
            past = [p for d, p in sorted(self.history.get(u, {}).items()) if d < day and p and p.get("iv")]
            ivs = [p["iv"] for p in past]
            iv_rank, pctl = rank(point["iv"], ivs)
            hv = hv20(closes.get(u, []) + ([point["spot"]] if day == today else []))
            ago = lambda n: round(point["iv"] - ivs[-n], 2) if len(ivs) >= n else None  # noqa: E731
            sym = spot_symbol(u)
            rows.append({"underlying": u, "kind": "index" if u in INDEX_SPOTS else "stock", "spot_symbol": sym,
                         "sector": sector_of(sym) if u not in INDEX_SPOTS else "Index", "day": day, **point,
                         "as_of": datetime.fromtimestamp(point["ts"], IST).isoformat(timespec="minutes"),
                         "skew": round(point["pe_iv"] - point["ce_iv"], 2) if point["pe_iv"] and point["ce_iv"] else None,
                         "iv_rank": iv_rank, "iv_pctl": pctl, "sessions": len(ivs) + 1,
                         "iv_low": min(ivs + [point["iv"]]), "iv_high": max(ivs + [point["iv"]]),
                         "iv_chg_1d": ago(1), "iv_chg_5d": ago(5), "hv20": hv,
                         "iv_hv": round(point["iv"] / hv, 2) if hv else None,
                         "series": [p["iv"] for d, p in sorted(self.history.get(u, {}).items())
                                    if d < day and p and p.get("iv")][-19:] + [point["iv"]]})
        rows.sort(key=lambda r: (r["kind"] != "index", -(r["iv_rank"] if r["iv_rank"] is not None else -1)))
        return {"as_of": now.isoformat(timespec="seconds"), "min_days": MIN_DAYS, "tenor_days": TENOR_DAYS, "min_sessions": MIN_SESSIONS,
                "rows": rows, "source": "stored option chains (index ~1/min since 3 Sep, stocks every 15 min since 26 Oct)"}
