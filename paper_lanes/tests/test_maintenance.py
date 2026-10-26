from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from paper_lanes.manager import Manager
from paper_lanes.maintenance import correct_untraded_starting_capital

NOW=datetime(2026,10,26,6,15,tzinfo=ZoneInfo('Asia/Kolkata'))


def test_correction_is_audited_idempotent_and_preserves_identity_policy(tmp_path):
    m=Manager(tmp_path,capital=1000000,now=NOW)
    old={lane:book.snapshot(NOW) for lane,book in m.books.items()}
    assert correct_untraded_starting_capital(tmp_path,1000000,5000000,NOW)
    assert not correct_untraded_starting_capital(tmp_path,1000000,5000000,NOW)
    restarted=Manager(tmp_path,capital=5000000,now=NOW)
    for lane,book in restarted.books.items():
        s=book.snapshot(NOW)
        assert s['account_id']==old[lane]['account_id'] and s['policy']==old[lane]['policy']
        assert s['initial_capital']==s['cash']==s['equity']==5000000
        assert s['return_pct']==s['max_drawdown_pct']==s['realized_pnl']==s['unrealized_pnl']==0
        assert s['equity_curve'][0]['equity_paise']==500000000 and not s['fills']
        assert len(s['events'])==1 and s['events'][0]['kind']=='STARTING_CAPITAL_CORRECTED'
        assert s['events'][0]['detail']['previous_equity_baselines'][0]['equity_paise']==100000000


def test_activity_in_either_book_prevents_changes_to_both(tmp_path):
    m=Manager(tmp_path,capital=1000000,now=NOW)
    with m.books['cbe'].transaction() as db:
        db.execute('INSERT INTO cohorts VALUES(?,?,0)',('prior-cohort',NOW.isoformat()))
    with pytest.raises(ValueError,match='activity'):
        correct_untraded_starting_capital(tmp_path,1000000,5000000,NOW)
    for book in m.books.values():
        assert book.snapshot(NOW)['initial_capital']==1000000 and not book.snapshot(NOW)['events']


def test_nonpristine_cash_cannot_be_rebased(tmp_path):
    m=Manager(tmp_path,capital=1000000,now=NOW)
    with m.books['vanguard'].transaction() as db:db.execute('UPDATE account SET cash_paise=cash_paise-1')
    with pytest.raises(ValueError,match='pristine'):
        correct_untraded_starting_capital(tmp_path,1000000,5000000,NOW)
    assert m.books['cbe'].snapshot(NOW)['cash']==1000000
