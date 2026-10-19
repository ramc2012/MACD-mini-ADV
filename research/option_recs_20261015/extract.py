"""Extract the data for the 15 Oct 2026 option-recommendation study (read-only).

Outputs (data/):
  opt30.csv.gz   option 30-minute signal bars, 09:15-aligned exactly as blast_engine._bucket
                 (symbol, expiry, bucket, open, high, low, close, volume, minutes)
  spot30.csv.gz  the same for every index/stock spot and India VIX
  spot1d.csv.gz  daily OHLC of the spots (for realized volatility and ATR)

Run: docker run --rm --memory 4g -v macd-mini-parallel_engine_data:/data -v $PWD:/w \
       -v <repo>/engine/src/macd_trader/universe.py:/w/universe.py:ro research-ml:latest python /w/extract.py
"""
import csv, gzip, sqlite3, sys, time
sys.path.insert(0, '/eng')
from universe import INDEX_SPOTS, STOCK_SPOTS

DB = 'file:/data/historical.sqlite3?mode=ro'
OPT_FROM = 1786041000          # 2026-08-07 00:00 IST: MACD/high warm-up before the 20 Aug band start
SPOT_FROM = 1782239400         # 2026-06-24 00:00 IST: 20+ sessions of daily history for RV/ATR
IST = 19800
OPEN_MIN, LAST_MIN = 555, 929  # 09:15 .. 15:29


def bucket_of(ts):
    ist = ts + IST
    day0 = ist - ist % 86400
    m = (ist - day0) // 60
    if m < OPEN_MIN or m > LAST_MIN:
        return None, None
    k = (m - OPEN_MIN) // 30
    return day0 - IST + (OPEN_MIN + 30 * k) * 60, day0 - IST


def fold(rows, out, extra):
    cur = None
    for ts, o, h, l, c, v in rows:
        b, _ = bucket_of(ts)
        if b is None:
            continue
        if cur and cur[0] == b:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v; cur[6] += 1
        else:
            if cur:
                out.writerow(extra + cur)
            cur = [b, o, h, l, c, v, 1]
    if cur:
        out.writerow(extra + cur)


def main():
    db = sqlite3.connect(DB, uri=True, timeout=30)
    t0 = time.time()
    syms = db.execute("""SELECT DISTINCT symbol, expiry FROM historical_candles
                         WHERE timeframe_seconds = 60
                           AND (symbol LIKE '%CE' OR symbol LIKE '%PE') AND timestamp >= ?""", (OPT_FROM,)).fetchall()
    print('option symbols', len(syms), round(time.time() - t0), 's', flush=True)
    with gzip.open('/w/data/opt30.csv.gz', 'wt', newline='') as f:
        w = csv.writer(f); w.writerow(['symbol', 'expiry', 'bucket', 'open', 'high', 'low', 'close', 'volume', 'minutes'])
        for i, (s, exp) in enumerate(syms):
            rows = db.execute("""SELECT timestamp, open, high, low, close, volume FROM historical_candles
                                 WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? ORDER BY timestamp""",
                              (s, OPT_FROM))
            fold(rows, w, [s, (exp or '')[:10]])
            if i % 1000 == 0:
                print(i, round(time.time() - t0), 's', flush=True)
    spots = list(INDEX_SPOTS.values()) + list(STOCK_SPOTS.values()) + ['NSE:INDIAVIX-INDEX']
    with gzip.open('/w/data/spot30.csv.gz', 'wt', newline='') as f30, gzip.open('/w/data/spot1d.csv.gz', 'wt', newline='') as f1:
        w30 = csv.writer(f30); w30.writerow(['symbol', 'bucket', 'open', 'high', 'low', 'close', 'volume', 'minutes'])
        w1 = csv.writer(f1); w1.writerow(['symbol', 'day0', 'open', 'high', 'low', 'close', 'volume', 'minutes'])
        for s in spots:
            rows = db.execute("""SELECT timestamp, open, high, low, close, volume FROM historical_candles
                                 WHERE symbol = ? AND timeframe_seconds = 60 AND timestamp >= ? ORDER BY timestamp""",
                              (s, SPOT_FROM)).fetchall()
            fold(rows, w30, [s])
            day = None
            for ts, o, h, l, c, v in rows:
                b, d0 = bucket_of(ts)
                if b is None:
                    continue
                if day and day[0] == d0:
                    day[2] = max(day[2], h); day[3] = min(day[3], l); day[4] = c; day[5] += v; day[6] += 1
                else:
                    if day:
                        w1.writerow([s] + day)
                    day = [d0, o, h, l, c, v, 1]
            if day:
                w1.writerow([s] + day)
    print('done', round(time.time() - t0), 's', flush=True)


main()
