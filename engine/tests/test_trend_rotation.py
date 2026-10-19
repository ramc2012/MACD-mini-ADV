import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader import trend_rotation as tr

IST = ZoneInfo('Asia/Kolkata')


def test_premiums_have_only_two_states_and_spots_have_three():
    assert tr.state(1, 1, 'PE') == 'up'
    assert tr.state(-1, -1, 'PE') == 'fading'
    assert tr.state(-1, -1, 'CE') == 'fading'
    assert tr.state(-1, -1, 'SPOT') == 'down'
    assert tr.state(1, -1, 'SPOT') == 'fading'
    assert tr.state(-1, 1, 'CE') == 'fading'
    assert tr.state(0, 0, 'SPOT') == 'fading'


def test_absolute_rise_and_fall_and_causal_tails():
    rising = [(i, 100+i) for i in range(60)]
    falling = [(i, 200-i) for i in range(60)]
    assert tr.points(rising, 'CE', 8)[-1]['state'] == 'up'
    assert tr.points(falling, 'PE', 8)[-1]['state'] == 'fading'
    assert tr.points(falling, 'SPOT', 8)[-1]['state'] == 'down'
    assert all(p['state'] == 'fading' for p in tr.points([(i, 100) for i in range(60)], 'CE', 8))
    prefix = tr.points(rising[:45], 'CE', 100)
    full = tr.points(rising, 'CE', 100)
    assert prefix == [p for p in full if p['timestamp'] < 45]


def history(path, symbol, prices):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INT, timestamp INT, open REAL, high REAL, low REAL, close REAL, volume INT)')
        # 30-minute samples are stored with their actual minute keys; gaps within a bar are allowed.
        start = datetime(2026, 10, 12, 9, 15, tzinfo=IST)
        rows = []
        for day in range(4):
            for bar in range(13):
                ts = int((start + timedelta(days=day, minutes=bar*30)).timestamp())
                p = prices[len(rows)]
                rows.append((symbol,60,ts,p,p,p,p,10))
        db.executemany('INSERT INTO historical_candles VALUES (?,?,?,?,?,?,?,?)', rows)
    return rows


def test_build_excludes_unfinished_and_future_candles_without_writing(tmp_path):
    path = tmp_path/'h.sqlite3'; symbol = 'NSE:SBIN26OCT800CE'
    rows = history(path, symbol, [100+i for i in range(52)])
    now = datetime(2026, 10, 15, 10, 0, tzinfo=IST)
    first = tr.build(str(path), [{'symbol':symbol, 'underlying':'SBIN'}], now=now)
    assert len(first['rows']) == 1
    assert first['rows'][0]['as_of'] == int(datetime(2026,10,15,9,45,tzinfo=IST).timestamp())
    with sqlite3.connect(path) as db:
        db.execute('UPDATE historical_candles SET close=999999 WHERE timestamp>=?',
                   (int(datetime(2026,10,15,9,45,tzinfo=IST).timestamp()),))
    assert tr.build(str(path), [{'symbol':symbol, 'underlying':'SBIN'}], now=now)['rows'] == first['rows']
    before = path.read_bytes()
    tr.build(str(path), [{'symbol':symbol}], now=now)
    assert path.read_bytes() == before


def test_stale_and_new_contracts_are_unavailable_not_fading(tmp_path):
    path = tmp_path/'h.sqlite3'; symbol = 'NSE:SBIN26OCT800CE'
    history(path, symbol, [100+i for i in range(52)])
    out = tr.build(str(path), [{'symbol':symbol}, {'symbol':'NSE:NEW26OCT100CE'}],
                   now=datetime(2026,10,16,16,0,tzinfo=IST))
    assert not out['rows']
    assert {r['reason'] for r in out['unavailable']} == {'Latest completed bar missing', 'No stored candles'}


def test_expected_completed_bar_respects_weekend_and_segment_close():
    saturday = datetime(2026,10,17,12,tzinfo=IST)
    _, end = tr.latest_completed('NSE:SBIN26OCT800CE',1800,saturday)
    assert datetime.fromtimestamp(end,IST) == datetime(2026,10,16,15,40,tzinfo=IST)
    _, end = tr.latest_completed('NSE:SBIN-EQ',86400,saturday)
    assert datetime.fromtimestamp(end,IST) == datetime(2026,10,16,15,15,tzinfo=IST)


def test_daily_spot_uses_completed_daily_prices(tmp_path):
    path=tmp_path/'h.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE daily_closes(symbol TEXT, day TEXT, close REAL)')
        day=datetime(2026,8,1,tzinfo=IST).date()
        from macd_trader import market_calendar
        prices=[]
        while day<=datetime(2026,10,16).date():
            if market_calendar.is_trading_day(day):
                prices.append(('NSE:SBIN-EQ',day.isoformat(),100+len(prices)))
            day+=timedelta(days=1)
        db.executemany('INSERT INTO daily_closes VALUES (?,?,?)',prices)
    out=tr.build(str(path),[{'symbol':'NSE:SBIN-EQ'}],kind='SPOT',timeframe=86400,
                 now=datetime(2026,10,17,12,tzinfo=IST))
    assert out['rows'][0]['state']=='up'
