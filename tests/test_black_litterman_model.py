"""Tests for the skfolio Black-Litterman model with synthetic prices."""

import unittest

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from skfolio.portfolio import Portfolio

from optimizacion_portafolios.models.black_litterman import (
    LONG_ONLY_OBJECTIVES,
    OBJECTIVES,
    TAU,
    TRADING_DAYS,
    market_implied_risk_aversion,
    optimize_black_litterman,
)
from support import make_prices

TICKERS = ("AAPL", "MSFT", "GOOGL", "XLV")
RISK_FREE_RATE = 0.02


class BlackLittermanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prices = make_prices(TICKERS, rows=400)
        self.market = make_prices(("SPY",), rows=400)["SPY"]
        self.caps = {"AAPL": 3e12, "MSFT": 3.5e12, "GOOGL": 2e12, "XLV": 4e10}
        self.views = {"AAPL": 0.12, "MSFT": 0.08, "GOOGL": 0.10, "XLV": 0.20}

    def optimize(self, objective: str = "max_sharpe", **overrides):
        arguments = {
            "prices": self.prices,
            "views": self.views,
            "confidences": {ticker: 0.95 for ticker in TICKERS},
            "objective": objective,
            "risk_free_rate": RISK_FREE_RATE,
            "market_caps": self.caps,
            "market_prices": self.market,
        }
        arguments.update(overrides)
        return optimize_black_litterman(**arguments)

    def test_long_only_objectives_are_fully_invested(self) -> None:
        for objective in LONG_ONLY_OBJECTIVES:
            with self.subTest(objective=objective):
                result = self.optimize(objective)
                self.assertAlmostEqual(result.weights.sum(), 1.0, places=4)
                self.assertTrue((result.weights >= -1e-6).all())
                self.assertEqual(list(result.weights.index), list(TICKERS))

    def test_unconstrained_utility_sums_to_one(self) -> None:
        result = self.optimize("max_utility_unconstrained")
        self.assertAlmostEqual(result.weights.sum(), 1.0, places=4)
        self.assertIn("max_utility_unconstrained", OBJECTIVES)

    def test_max_return_concentrates_in_best_posterior_asset(self) -> None:
        result = self.optimize("max_return")
        self.assertAlmostEqual(
            result.weights[result.posterior_returns.idxmax()], 1.0, places=4
        )

    def test_bullish_view_raises_posterior_return(self) -> None:
        neutral = self.optimize(views={ticker: 0.08 for ticker in TICKERS})
        bullish = self.optimize(
            views={**{ticker: 0.08 for ticker in TICKERS}, "XLV": 0.40}
        )
        self.assertGreater(
            bullish.posterior_returns["XLV"], neutral.posterior_returns["XLV"]
        )

    def test_equilibrium_views_recover_market_weights(self) -> None:
        prior = self.optimize("max_utility").prior_returns
        result = self.optimize("max_utility", views=prior.to_dict())
        np.testing.assert_allclose(result.posterior_returns, prior, atol=1e-10)
        # Not exact: the posterior covariance adds estimation uncertainty.
        np.testing.assert_allclose(result.weights, result.market_weights, atol=2e-3)

    def test_prior_is_market_cap_equilibrium(self) -> None:
        result = self.optimize()
        returns = self.prices.pct_change().dropna()
        covariance = LedoitWolf().fit(returns).covariance_
        weights = np.array([self.caps[ticker] for ticker in TICKERS])
        weights = weights / weights.sum()
        delta = market_implied_risk_aversion(self.market, RISK_FREE_RATE)
        expected = (delta * covariance @ weights + RISK_FREE_RATE / TRADING_DAYS) * 252
        np.testing.assert_allclose(result.prior_returns, expected, rtol=1e-8)
        np.testing.assert_allclose(result.market_weights, weights)

    def test_ninety_five_percent_confidence_uses_idzorek_omega(self) -> None:
        result = self.optimize()
        self.assertTrue((result.view_confidences == 0.95).all())
        returns = self.prices.pct_change().dropna()
        sigma = LedoitWolf().fit(returns).covariance_
        daily_rf = RISK_FREE_RATE / TRADING_DAYS
        prior = result.prior_returns.to_numpy() / TRADING_DAYS - daily_rf
        views = np.array([self.views[t] for t in TICKERS]) / TRADING_DAYS - daily_rf
        omega = np.diag(TAU * (1 / 0.95 - 1) * np.diag(sigma))
        gain = TAU * sigma @ np.linalg.inv(TAU * sigma + omega)
        expected = (prior + gain @ (views - prior) + daily_rf) * TRADING_DAYS
        np.testing.assert_allclose(result.posterior_returns, expected, rtol=1e-8)

    def test_higher_confidence_moves_posterior_closer_to_view(self) -> None:
        strong = self.optimize()
        weak = self.optimize(confidences={ticker: 0.30 for ticker in TICKERS})
        view = self.views["XLV"]
        self.assertLess(
            abs(strong.posterior_returns["XLV"] - view),
            abs(weak.posterior_returns["XLV"] - view),
        )

    def test_view_conversion_round_trips_annual_values(self) -> None:
        confident = self.optimize(
            confidences={ticker: 0.999999 for ticker in TICKERS}
        )
        np.testing.assert_allclose(
            confident.posterior_returns, pd.Series(self.views), atol=1e-4
        )

    def test_sortino_matches_skfolio_portfolio_on_posterior_scenarios(self) -> None:
        result = self.optimize("max_sortino")
        returns = self.prices.pct_change().dropna()
        daily_mu = result.posterior_returns / TRADING_DAYS
        scenarios = returns - returns.mean() + daily_mu
        portfolio = Portfolio(
            X=scenarios,
            weights=result.weights.to_numpy(),
            risk_free_rate=RISK_FREE_RATE / TRADING_DAYS,
        )
        self.assertAlmostEqual(
            result.sortino, portfolio.annualized_sortino_ratio, places=8
        )
        self.assertTrue(np.isfinite(result.sortino))
        sharpe_portfolio = self.optimize("max_sharpe")
        self.assertGreaterEqual(result.sortino, sharpe_portfolio.sortino - 1e-3)

    def test_posterior_correlation_matches_covariance(self) -> None:
        result = self.optimize()
        correlation = result.posterior_correlation
        covariance = result.posterior_covariance
        self.assertEqual(list(correlation.index), list(TICKERS))
        np.testing.assert_allclose(np.diag(correlation), 1.0)
        np.testing.assert_allclose(correlation, correlation.T, atol=1e-12)
        self.assertTrue(((correlation >= -1) & (correlation <= 1)).all().all())
        expected = covariance.loc["AAPL", "MSFT"] / np.sqrt(
            covariance.loc["AAPL", "AAPL"] * covariance.loc["MSFT", "MSFT"]
        )
        self.assertAlmostEqual(correlation.loc["AAPL", "MSFT"], expected, places=12)

    def test_requires_one_view_per_asset(self) -> None:
        with self.assertRaisesRegex(ValueError, "una view para cada activo"):
            self.optimize(views={"AAPL": 0.08})

    def test_rejects_invalid_market_caps(self) -> None:
        with self.assertRaisesRegex(ValueError, "capitalizaciones deben ser"):
            self.optimize(market_caps={**self.caps, "XLV": 0.0})
        with self.assertRaisesRegex(ValueError, "una capitalización para cada"):
            self.optimize(market_caps={"AAPL": 1e12})

    def test_rejects_non_positive_risk_aversion(self) -> None:
        falling = pd.Series(
            np.linspace(100, 60, len(self.market)), index=self.market.index
        )
        with self.assertRaisesRegex(ValueError, "aversión al riesgo positiva"):
            self.optimize(market_prices=falling)

    def test_rejects_unknown_objective(self) -> None:
        with self.assertRaisesRegex(ValueError, "El objetivo debe ser"):
            self.optimize("model_weights")


if __name__ == "__main__":
    unittest.main()
