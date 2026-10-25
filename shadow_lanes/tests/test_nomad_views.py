import hashlib
import json
import pytest

from fastapi.testclient import TestClient

from shadow_lanes.service import create_app
from shadow_lanes.store import ShadowStore


def test_fresh_scope_cannot_read_funded_books(tmp_path, monkeypatch):
    from shadow_lanes import source
    monkeypatch.setattr(source, 'connect', lambda **kw: (_ for _ in ()).throw(AssertionError('Fresh portfolio must not query a retained account')))
    monkeypatch.setenv('MACD_API_TOKEN', 'isolated-test-token')
    client=TestClient(create_app(ShadowStore(tmp_path/'fresh.sqlite'), False))
    headers={'X-Macd-Token':'isolated-test-token'}
    assert client.get('/api/nomad/vanguard/paper').status_code == 401
    for path in ('vanguard/paper','vanguard/book','cbe/paper-summary','cbe/paper-positions'):
        response=client.get('/api/nomad/'+path, headers=headers)
        assert response.status_code == 200
        assert response.json()['shadow_only'] is True
        assert client.post('/api/nomad/'+path, headers=headers).status_code == 405
    assert client.get('/api/nomad/cbe/latest?scope=invalid',headers=headers).status_code == 400


def test_retained_cbe_gets_preserve_book_bytes(tmp_path, monkeypatch):
    book=tmp_path/'paper_positions.json'
    book.write_text(json.dumps({'open_positions':[], 'closed_positions':[], 'peak_equity':1000000, 'max_drawdown':0}))
    before=hashlib.sha256(book.read_bytes()).hexdigest()
    monkeypatch.setenv('LEGACY_CBE_FILE',str(book))
    monkeypatch.setenv('MACD_API_TOKEN','')
    client=TestClient(create_app(ShadowStore(tmp_path/'fresh.sqlite'),False))
    summary=client.get('/api/nomad/cbe/paper-summary?scope=retained').json()
    assert summary['initial_capital'] == 1000000
    assert summary['admissions_blocked'] is True
    assert summary['total_equity'] == 1000000
    assert client.get('/api/nomad/cbe/paper-positions?scope=retained').json()['read_only'] is True
    assert client.get('/api/nomad/cbe/latest?scope=retained').status_code == 503
    assert hashlib.sha256(book.read_bytes()).hexdigest() == before
    assert client.get('/api/nomad/cbe/paper-summary').json()['initial_capital'] is None


def test_reader_connection_failure_is_safe_unavailable(monkeypatch):
    from fastapi import HTTPException
    from shadow_lanes import nomad_read
    def unavailable(**kwargs):
        assert kwargs['readonly'] is True
        raise OSError('private connection detail')
    monkeypatch.setattr(nomad_read.source,'connect',unavailable)
    with pytest.raises(HTTPException) as caught:
        nomad_read.read('SELECT 1',{})
    assert caught.value.status_code==503
    assert 'private' not in caught.value.detail
