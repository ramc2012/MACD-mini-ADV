"""One-time: seed agent_index_days in the live historical.sqlite3, then train the index lane (scorecard)."""
import json, sqlite3
from macd_trader import index_scorecard as sc, index_trades
db = sqlite3.connect('/app/data/historical.sqlite3', timeout=60)
sc.ensure_schema(db)
before = db.execute('SELECT COUNT(*) FROM agent_index_days').fetchone()[0]
seed = json.load(open('/tmp/seed.json'))
with db:
    db.executemany("INSERT OR IGNORE INTO agent_index_days (underlying, day, open, high, low, close, features, ce_path, ce_r, pe_path, pe_r, ce_sel, pe_sel, source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   [(r['underlying'], r['day'], r['open'], r['high'], r['low'], r['close'], json.dumps(r['features']) if r.get('features') else None,
                     r.get('ce_path'), r.get('ce_r'), r.get('pe_path'), r.get('pe_r'), r.get('ce_sel'), r.get('pe_sel'), 'fyers-research', 'seed-2026-10-12') for r in seed])
print('rows before', before, 'after seed', db.execute('SELECT COUNT(*) FROM agent_index_days').fetchone()[0])
print(json.dumps(index_trades.train(db), default=str)[:1500])
