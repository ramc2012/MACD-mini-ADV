"""Synthetic causal/data-integrity checks; no real outcome inspection."""
import unittest
import numpy as np
import pandas as pd

from premium_ratio_research import (aggregate_day, clean_minutes, epoch,
    forward_return, parse_contract, ratio_frame, ridge_fit, ridge_predict,
    select_ladder, select_pair, pair_ratio_frame, split_dates)


def minutes(start, count, price=100.0):
    return pd.DataFrame({"timestamp": np.arange(start, start + count * 60, 60),
        "open": price, "high": price, "low": price, "close": price, "volume": 10})


class RatioResearchTests(unittest.TestCase):
    def setUp(self):
        self.open = epoch("2026-08-03", 9, 15)

    def test_monthly_and_weekly_parse(self):
        monthly = {"NSE": "2026-08-25", "BSE": "2026-08-27"}
        self.assertEqual(parse_contract("NSE:360ONE26AUG1100CE", monthly)["root"], "360ONE")
        self.assertEqual(parse_contract("NSE:NIFTY2681824000PE", monthly)["expiry"], "2026-08-18")
        self.assertEqual(parse_contract("NSE:HINDPETRO26AUG370.75PE", monthly)["strike"], 370.75)
        self.assertIsNone(parse_contract("NSE:NIFTY26SEP25000CE", monthly))

    def test_empty_cleaning(self):
        self.assertIn("day", clean_minutes(minutes(self.open, 0)).columns)

    def test_regular_session_and_bad_ohlc(self):
        raw = minutes(self.open - 60, 4)
        raw.loc[2, "low"] = 101
        clean = clean_minutes(raw)
        self.assertEqual(clean.index.tolist(), [self.open, self.open + 120])

    def test_30min_bars_anchored_to_0915_and_partial_dropped(self):
        raw = clean_minutes(minutes(self.open, 375))
        bars = aggregate_day(raw, self.open, 30)
        self.assertEqual(bars.index[0], epoch("2026-08-03", 9, 45))
        self.assertEqual(bars.index[-1], epoch("2026-08-03", 15, 15))
        self.assertEqual(len(bars), 12)
        self.assertTrue(bars.coverage.eq(1).all())

    def test_missing_closing_minute_not_forward_filled(self):
        raw = clean_minutes(minutes(self.open, 5)).drop(self.open + 240)
        self.assertTrue(aggregate_day(raw, self.open, 5).empty)

    def test_missing_internal_minute_is_reported(self):
        raw = clean_minutes(minutes(self.open, 5)).drop(self.open + 60)
        self.assertEqual(aggregate_day(raw, self.open, 5).coverage.iloc[0], .8)

    def test_ratio_identity_and_gap(self):
        legs = {role: aggregate_day(clean_minutes(minutes(self.open, 20, price)), self.open, 5)
                for role, price in (("itm", 120), ("atm", 80), ("otm", 40))}
        legs["otm"] = legs["otm"].drop(self.open + 600)
        ratios = ratio_frame(legs, 5)
        self.assertTrue(np.allclose(ratios.itm_atm * ratios.atm_otm, ratios.itm_otm))
        self.assertTrue(np.isnan(ratios.loc[self.open + 900, "dlog_itm_otm"]))
        self.assertEqual(ratios.loc[self.open + 1200, "dlog_itm_otm"], 0)

    def test_asof_ladder_ignores_future_listings_and_prices(self):
        meta, frames = [], {}
        for side in ("CE", "PE"):
            for strike in (98, 100, 102):
                symbol = f"NSE:TEST26AUG{strike}{side}"
                meta.append(dict(side=side, strike=strike, symbol=symbol))
                frames[symbol] = clean_minutes(minutes(self.open, 60))
        instant = self.open + 29 * 60
        expected = select_ladder(meta, frames, instant, 100, "CE")
        self.assertEqual(expected["itm"]["strike"], 98)
        self.assertEqual(select_ladder(meta, frames, instant, 100, "PE")["itm"]["strike"], 102)
        future = "NSE:TEST26AUG99CE"
        meta.append(dict(side="CE", strike=99, symbol=future))
        frames[future] = clean_minutes(minutes(instant + 60, 30))
        for frame in frames.values():
            frame.loc[frame.index > instant, "close"] = 10000
        self.assertEqual(select_ladder(meta, frames, instant, 100, "CE"), expected)

    def test_forward_entry_is_next_minute_open_not_signal_close(self):
        raw = clean_minutes(minutes(self.open, 40))
        decision = self.open + 5 * 60
        raw.loc[decision, "open"] = 110
        raw.loc[decision - 60, "close"] = 1
        raw.loc[decision + 29 * 60, "close"] = 121
        self.assertAlmostEqual(forward_return(raw, decision, 30), 10)
        raw = raw.drop(decision + 15 * 60)
        self.assertTrue(np.isnan(forward_return(raw, decision, 30)))

    def test_two_leg_does_not_fabricate_atm_ratios(self):
        legs = {role: aggregate_day(clean_minutes(minutes(self.open, 10, price)), self.open, 5)
                for role, price in (("itm", 120), ("otm", 40))}
        pair = pair_ratio_frame(legs, 5, "itm")
        self.assertEqual(pair.itm_otm.iloc[0], 3)
        self.assertNotIn("itm_atm", pair.columns)
        self.assertNotIn("atm_otm", pair.columns)

    def test_two_leg_requires_actual_strikes_bracketing_spot(self):
        rows = [dict(symbol=str(strike), strike=strike, side="PE") for strike in (99, 101)]
        data = {r["symbol"]: clean_minutes(minutes(self.open, 30)) for r in rows}
        pair = select_pair(rows, data, self.open+29*60, 100, "PE")
        self.assertEqual(pair["itm"]["strike"], 101)
        self.assertEqual(pair["otm"]["strike"], 99)
        self.assertIsNone(select_pair(rows, data, self.open+29*60, 102, "PE"))

    def test_split_dates_do_not_shuffle(self):
        frame = pd.DataFrame({"day": ["2026-08-13", "2026-08-14", "2026-08-18", "2026-08-19"]})
        self.assertEqual(split_dates(frame).tolist(), ["train", "validation", "validation", "test"])

    def test_test_outlier_does_not_refit_scaling(self):
        train = pd.DataFrame({"x": np.arange(100), "y": np.arange(100) * 2})
        model = ridge_fit(train, ["x"], "y")
        mean = model["mean"].copy()
        ridge_predict(model, pd.DataFrame({"x": [1e9, -1e9]}))
        np.testing.assert_array_equal(model["mean"], mean)
        np.testing.assert_array_equal(model["mean"], ridge_fit(train, ["x"], "y")["mean"])


if __name__ == "__main__":
    unittest.main()
