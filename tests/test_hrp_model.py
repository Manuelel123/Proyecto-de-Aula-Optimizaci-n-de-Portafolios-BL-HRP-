"""Tests for the HRP model with synthetic prices."""

import unittest

from optimizacion_portafolios.models.hrp import (
    calculate_hrp,
    calculate_hrp_contributions,
)
from support import make_prices

TICKERS = ("AAPL", "MSFT", "GOOGL", "XLV")


class HrpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.returns = make_prices(TICKERS).pct_change(fill_method=None).dropna()

    def test_weights_are_long_only_and_fully_invested(self) -> None:
        weights, correlation = calculate_hrp(self.returns)
        self.assertAlmostEqual(weights.sum(), 1.0)
        self.assertTrue((weights > 0).all())
        self.assertEqual(set(correlation.index), set(TICKERS))
        self.assertEqual(list(correlation.index), list(correlation.columns))

    def test_contributions_add_up_to_portfolio_return(self) -> None:
        weights, _ = calculate_hrp(self.returns)
        _, summary = calculate_hrp_contributions(self.returns, weights)
        self.assertAlmostEqual(summary["Participación del retorno"].sum(), 1.0)
        self.assertAlmostEqual(
            summary["Contribución anualizada"].sum(),
            self.returns.dot(weights).mean() * 252,
        )


if __name__ == "__main__":
    unittest.main()
