"""Regression tests for timing/missing-path corrections, without running ML studies."""
import ast
from pathlib import Path
import unittest
import numpy as np
import pandas as pd


class CausalEod(unittest.TestCase):
    def setUp(self):
        source = ast.parse(Path(__file__).with_name('study2.py').read_text())
        function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'real_trade')
        self.days = pd.bdate_range('2026-09-01', periods=8)
        rows = []
        for i, day in enumerate(self.days):
            rows.append({'day': day, 'expiry': self.days[-1], 'strike': 100,
                         'underlying_price': 100, 'close': 10 + i, 'volume': 100, 'iv': .2})
        self.rows = pd.DataFrame(rows)
        self.ns = {'np': np, 'days_index': self.days, 'row_vol': lambda *_: .2,
                   'by_key': {('X', 'CE'): self.rows}}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'study2.py', 'exec'), self.ns)

    def trade(self):
        return self.ns['real_trade']('X', 'CE', self.days[0], 2)

    def test_signal_cannot_fill_same_close_and_costs_apply_both_sides(self):
        trade = self.trade()
        self.assertEqual(trade['entry_day'], str(self.days[1]))
        self.assertEqual(trade['exit_day'], str(self.days[3]))
        self.assertAlmostEqual(trade['ret'], 13*.985/(11*1.015)-1)

    def test_missing_exit_cannot_use_an_older_print(self):
        self.ns['by_key'][('X', 'CE')] = self.rows[self.rows.day != self.days[3]]
        self.assertIsNone(self.trade())

    def test_zero_volume_and_missing_entry_are_unavailable(self):
        self.rows.loc[self.rows.day == self.days[3], 'volume'] = 0
        self.assertIsNone(self.trade())
        self.ns['by_key'][('X', 'CE')] = self.rows[self.rows.day != self.days[1]]
        self.assertIsNone(self.trade())

    def test_contract_must_be_known_on_signal_day(self):
        self.ns['by_key'][('X', 'CE')] = self.rows[self.rows.day != self.days[0]]
        self.assertIsNone(self.trade())


if __name__ == '__main__':
    unittest.main()
