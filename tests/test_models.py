"""Tests for the optimization models with synthetic prices."""

import unittest

from optimizacion_portafolios.models.black_litterman import optimizar_black_litterman
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


class BlackLittermanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prices = make_prices(TICKERS, rows=400)
        self.market = make_prices(("SPY",), rows=400)["SPY"]
        self.caps = {"AAPL": 3e12, "MSFT": 3.5e12, "GOOGL": 2e12, "XLV": 4e10}

    def optimize(self, objective: str, views: dict | None = None):
        return optimizar_black_litterman(
            precios=self.prices,
            vistas_absolutas=views or {ticker: 0.08 for ticker in TICKERS},
            confianzas={ticker: 0.95 for ticker in TICKERS},
            objetivo=objective,
            tasa_libre_riesgo=0.02,
            capitalizaciones=self.caps,
            precios_mercado=self.market,
        )

    def test_long_only_objectives_are_fully_invested(self) -> None:
        for objective in ("max_sharpe", "min_volatility"):
            with self.subTest(objective=objective):
                result = self.optimize(objective)
                self.assertAlmostEqual(result.pesos.sum(), 1.0, places=4)
                self.assertTrue((result.pesos >= -1e-8).all())
                self.assertEqual(list(result.pesos.index), list(TICKERS))

    def test_bullish_view_raises_posterior_return(self) -> None:
        neutral = self.optimize("max_sharpe")
        bullish = self.optimize(
            "max_sharpe", {**{ticker: 0.08 for ticker in TICKERS}, "XLV": 0.40}
        )
        self.assertGreater(
            bullish.retornos_posteriores["XLV"], neutral.retornos_posteriores["XLV"]
        )

    def test_requires_one_view_per_asset(self) -> None:
        with self.assertRaisesRegex(ValueError, "una view para cada activo"):
            self.optimize("max_sharpe", {"AAPL": 0.08})


if __name__ == "__main__":
    unittest.main()
