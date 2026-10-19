"""Read-only original-data extract; run inside the isolated engine image."""
import collections
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import sqlite3
import sys
from zoneinfo import ZoneInfo

from macd_trader.sessions import in_session, session_close

ROOT = pathlib.Path('/study')
IST = ZoneInfo('Asia/Kolkata')
STREAM = '--stream' in sys.argv
db = sqlite3.connect('file:/app/data/historical.sqlite3?mode=ro' if STREAM else 'file:/source/historical.sqlite3?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
counts = collections.Counter()
metadata = {'source': 'macd-mini-parallel_engine_data/historical.sqlite3',
            'table': 'historical_candles', 'snapshot_utc': dt.datetime.now(dt.UTC).isoformat(),
            'bar_timestamp': 'session-anchored start; feature availability at bar end',
            'volume_unit': 'source field, retained without reinterpretation', 'series': []}
query = '''SELECT symbol,timestamp,open,high,low,close,volume,asset_type,expiry
FROM historical_candles WHERE timeframe_seconds=60 ORDER BY symbol,timestamp'''
symbol = None
bars = []
bucket = None
agg = None
asset = expiry = None

def flush_series(out):
    global bars
    if not symbol or not bars:
        return
    record = {'symbol': symbol, 'asset': asset, 'expiry': expiry, 'bars': bars}
    out.write(json.dumps(record, separators=(',', ':')) + '\n')
    metadata['series'].append({'symbol': symbol, 'asset': asset, 'expiry': expiry,
                               'bars5': len(bars), 'start': bars[0][0], 'end': bars[-1][0]})
    if len(metadata['series']) % 250 == 0:
        print('exported', len(metadata['series']), 'series', file=sys.stderr, flush=True)
    bars = []

target = ROOT / 'candles5.jsonl.gz'
import io
sink = io.TextIOWrapper(gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=3)) if STREAM else gzip.open(target, 'wt', compresslevel=3)
with sink as out:
    for sym, ts, o, h, l, c, v, typ, exp in db.execute(query):
        counts['raw'] += 1
        if sym != symbol:
            if agg is not None:
                bars.append(agg)
            flush_series(out)
            symbol, asset, expiry, bucket, agg = sym, typ, exp, None, None
        moment = dt.datetime.fromtimestamp(ts, dt.UTC).astimezone(IST)
        if not in_session(sym, moment):
            counts['off_session'] += 1
            continue
        if not (0 < l <= min(o,c) <= max(o,c) <= h):
            counts['invalid_ohlc'] += 1
            continue
        minute = moment.hour * 60 + moment.minute
        start = ts - (minute - 555) % 5 * 60
        end = min(start + 300, int(dt.datetime.combine(moment.date(), session_close(sym,moment.date()), IST).timestamp()))
        if start != bucket:
            if agg is not None:
                bars.append(agg)
            agg = [start,o,h,l,c,v,1,end]
            bucket = start
        else:
            agg[2] = max(agg[2],h)
            agg[3] = min(agg[3],l)
            agg[4] = c
            agg[5] += v
            agg[6] += 1
    if agg is not None:
        bars.append(agg)
    flush_series(out)
db.rollback()
db.close()
metadata['counts'] = dict(counts)
if STREAM:
    print('MANIFEST ' + json.dumps(metadata,separators=(',',':')), file=sys.stderr,flush=True)
else:
    metadata['sha256'] = hashlib.file_digest(open(target,'rb'),'sha256').hexdigest()
    (ROOT/'source_manifest.json').write_text(json.dumps(metadata, indent=2))
    print(json.dumps({'counts':dict(counts),'series':len(metadata['series']),'sha256':metadata['sha256']}),flush=True)
