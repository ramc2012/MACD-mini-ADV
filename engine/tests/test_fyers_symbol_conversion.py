from macd_trader.brokers import _make_resilient_symbol_conversion


def test_large_symbol_conversion_retries_and_combines_all_chunks(monkeypatch):
    monkeypatch.setattr("macd_trader.brokers.time.sleep", lambda _: None)
    calls = []

    def original(_, symbols):
        calls.append(symbols)
        if len(calls) == 2:
            return None  # FYERS SDK returns None after a transient TLS failure.
        return ({name: name for name in symbols}, [], False, "")

    symbols = [f"NSE:TEST{i}-EQ" for i in range(214)]
    converted, invalid, index_issue, error = _make_resilient_symbol_conversion(original)(None, symbols)
    assert set(converted) == set(symbols)
    assert not invalid and not index_issue and not error
    assert [len(chunk) for chunk in calls] == [100, 100, 100, 14]
