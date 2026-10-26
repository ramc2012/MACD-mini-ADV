"""Offline, explicitly authorized corrections before either paper book has traded.

Stop the paper-lane service and back up both databases before calling this function.
There is deliberately no HTTP route or automatic configuration-driven adjustment.
"""
import json
import sqlite3
from pathlib import Path

from .book import money


def correct_untraded_starting_capital(folder, expected_capital, capital, now):
    target,expected=money(capital),money(expected_capital)
    if target<=0 or expected<=0 or now.tzinfo is None:
        raise ValueError('Positive capital and a timezone-aware audit timestamp required')
    folder=Path(folder)
    paths=[folder/(lane+'.sqlite3') for lane in ('vanguard','cbe')]
    if not all(p.is_file() for p in paths):raise ValueError('Both existing books are required')
    db=sqlite3.connect(paths[0],timeout=5);db.row_factory=sqlite3.Row
    try:
        db.execute('ATTACH DATABASE ? AS cbe',(str(paths[1]),))
        db.execute('BEGIN IMMEDIATE')
        accounts={name:dict(db.execute(f'SELECT * FROM {name}.account WHERE id=1').fetchone()) for name in ('main','cbe')}
        if all(a['initial_paise']==target for a in accounts.values()):
            db.rollback();return False
        baselines={}
        for name,a in accounts.items():
            if a['initial_paise']!=expected or a['cash_paise']!=expected or a['peak_paise']!=expected or a['max_drawdown']!=0:
                raise ValueError('Account differs from the expected pristine starting balance')
            for table in ('orders','cohorts','positions','fills','decisions','settlements'):
                if db.execute(f'SELECT 1 FROM {name}.{table} LIMIT 1').fetchone():
                    raise ValueError('Starting capital cannot be corrected after candidate or trading activity')
            baselines[name]=[dict(r) for r in db.execute(f'SELECT * FROM {name}.equity ORDER BY at')]
            if not baselines[name] or any(r['equity_paise']!=expected or r['cash_paise']!=expected for r in baselines[name]):
                raise ValueError('Equity history is not a pristine cash baseline')
        for name,a in accounts.items():
            db.execute(f'UPDATE {name}.account SET initial_paise=?,cash_paise=?,peak_paise=? WHERE id=1',(target,target,target))
            db.execute(f'UPDATE {name}.equity SET equity_paise=?,cash_paise=?',(target,target))
            detail={'authorization':'Owner selected starting capital of INR 50 lakh per lane',
                    'previous_initial_paise':expected,'initial_paise':target,'account_id':a['account_id'],
                    'previous_equity_baselines':baselines[name],
                    'reason':'Pre-trade starting-capital correction; no investment return or trade generated'}
            db.execute(f'INSERT INTO {name}.events(at,kind,order_id,detail) VALUES(?,?,NULL,?)',
                       (now.isoformat(),'STARTING_CAPITAL_CORRECTED',json.dumps(detail)))
        db.commit();return True
    except BaseException:
        db.rollback();raise
    finally:db.close()
