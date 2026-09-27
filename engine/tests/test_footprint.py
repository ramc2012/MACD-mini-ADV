from macd_trader.footprint import (FootprintBook, IMBALANCE_RATIO,
                                   MAX_DETAIL_SYMBOLS, MIN_IMBALANCE_VOLUME)


def test_bid_ask_convention_and_delta():
    """Ask volume = buyer lifted the offer; bid volume = seller hit the bid."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 100.00, 50, 1)    # buy
    book.on_print("X", 1_755_000_010, 100.00, 20, -1)   # sell
    bar = book.payload("X")["bars"][0]
    level = next(r for r in bar["levels"] if r["p"] == 100.00)
    assert level["ask"] == 50 and level["bid"] == 20 and level["d"] == 30
    assert bar["delta"] == 30 and bar["v"] == 70


def test_diagonal_imbalance_compares_against_the_tick_below():
    """The platform convention: ask at P vs bid at P-1 tick, not P vs P."""
    book = FootprintBook(timeframe_seconds=60, tick_size=0.05)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 100.00, MIN_IMBALANCE_VOLUME, -1)          # bid at 100.00
    book.on_print("X", 1_755_000_001, 100.05,
                  MIN_IMBALANCE_VOLUME * IMBALANCE_RATIO, 1)                      # ask at 100.05
    levels = {r["p"]: r for r in book.payload("X")["bars"][0]["levels"]}
    assert levels[100.05]["imb"] == "buy"
    assert levels[100.00]["imb"] is None


def test_bars_bucket_by_timeframe_and_carry_session_cvd():
    book = FootprintBook(timeframe_seconds=300)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 10.0, 100, 1)
    book.on_print("X", 1_755_000_299, 10.5, 100, 1)     # same 5m bucket
    book.on_print("X", 1_755_000_300, 11.0, 40, -1)     # next bucket
    bars = book.payload("X")["bars"]
    assert len(bars) == 2
    assert bars[0]["v"] == 200 and bars[0]["cvd"] == 200
    assert bars[1]["cvd"] == 160                        # cumulative across bars


def test_detail_set_is_bounded_and_evicts_least_recently_watched():
    book = FootprintBook()
    for i in range(MAX_DETAIL_SYMBOLS + 4):
        book.watch(f"S{i}")
    assert len(book.bars) == MAX_DETAIL_SYMBOLS
    assert not book.watching("S0")            # evicted
    assert book.watching(f"S{MAX_DETAIL_SYMBOLS + 3}")


def test_unwatched_symbols_are_ignored_entirely():
    book = FootprintBook()
    book.on_print("NOTWATCHED", 1_755_000_000, 10.0, 100, 1)
    assert book.payload("NOTWATCHED")["bars"] == []


def test_bar_poc_is_the_highest_volume_price():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 10.00, 10, 1)
    book.on_print("X", 1_755_000_001, 10.05, 90, 1)
    bar = book.payload("X")["bars"][0]
    assert bar["poc"] == 10.05
    assert next(r for r in bar["levels"] if r["p"] == 10.05)["poc"] is True


def test_watch_seeds_from_retained_classified_prints():
    """Opening a chart must show real clusters at once. Seeding replays prints
    the flow tracker already classified — never candles, which have no side."""
    from macd_trader.orderflow import Print

    book = FootprintBook(timeframe_seconds=60)
    seed = [
        Print(1_755_000_000, 10.00, 40, -1, "quote"),
        Print(1_755_000_005, 10.05, 90, 1, "quote"),
    ]
    book.watch("X", seed_prints=seed)
    payload = book.payload("X")
    assert len(payload["bars"]) == 1
    bar = payload["bars"][0]
    assert bar["v"] == 130 and bar["delta"] == 50 and bar["cvd"] == 50
    assert len(payload["tape"]) == 2


def test_re_watching_does_not_duplicate_seeded_prints():
    from macd_trader.orderflow import Print

    book = FootprintBook(timeframe_seconds=60)
    seed = [Print(1_755_000_000, 10.0, 40, 1, "quote")]
    book.watch("X", seed_prints=seed)
    book.watch("X", seed_prints=seed)          # second open of the same chart
    assert book.payload("X")["bars"][0]["v"] == 40


def test_read_timeframe_aggregates_without_resetting_capture():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 10.0, 40, 1)
    book.on_print("X", 1_755_000_060, 10.05, 20, -1)

    one_minute = book.payload("X", bars=10, timeframe_seconds=60)
    five_minute = book.payload("X", bars=10, timeframe_seconds=300)
    one_minute_again = book.payload("X", bars=10, timeframe_seconds=60)

    assert len(one_minute["bars"]) == 2
    assert len(five_minute["bars"]) == 1
    assert five_minute["bars"][0]["v"] == 60
    assert one_minute_again == one_minute
