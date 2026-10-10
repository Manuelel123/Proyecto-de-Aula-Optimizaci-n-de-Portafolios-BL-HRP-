"""Tests for the HRP model with synthetic prices."""

import unittest

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

from optimizacion_portafolios.models.hrp import (
    WeightBounds,
    calculate_hrp,
    calculate_hrp_contributions,
    calculate_risk_contributions,
    optimize_hrp,
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

    def test_correlation_follows_single_linkage_leaf_order(self) -> None:
        _, correlation = calculate_hrp(self.returns)
        corr = self.returns.corr()
        distance = np.sqrt(np.clip((1 - corr) / 2, 0, 1))
        tree = linkage(squareform(distance, checks=False), "single")
        expected = [self.returns.columns[i] for i in leaves_list(tree)]
        self.assertEqual(list(correlation.index), expected)
        self.assertEqual(list(correlation.columns), expected)

    def test_unconstrained_weights_match_inverse_variance_bisection(self) -> None:
        weights, correlation = calculate_hrp(self.returns)
        covariance = self.returns.cov()
        expected = pd.Series(1.0, index=correlation.index)
        clusters = [list(correlation.index)]
        while clusters:
            clusters = [
                half
                for cluster in clusters
                if len(cluster) > 1
                for half in (cluster[: len(cluster) // 2], cluster[len(cluster) // 2 :])
            ]
            for left, right in zip(clusters[::2], clusters[1::2]):
                left_var, right_var = (
                    _cluster_variance(covariance, side) for side in (left, right)
                )
                alpha = 1 - left_var / (left_var + right_var)
                expected[left] *= alpha
                expected[right] *= 1 - alpha
        pd.testing.assert_series_equal(
            weights.sort_index(), expected.sort_index(), check_names=False
        )

    def test_global_bounds_are_respected(self) -> None:
        bounds = WeightBounds(min_weight=0.15, max_weight=0.30)
        weights, _ = calculate_hrp(self.returns, bounds)
        self.assertAlmostEqual(weights.sum(), 1.0)
        self.assertTrue((weights >= 0.15 - 1e-9).all())
        self.assertTrue((weights <= 0.30 + 1e-9).all())

    def test_asset_bounds_take_precedence_over_global(self) -> None:
        unconstrained, _ = calculate_hrp(self.returns)
        heaviest = unconstrained.idxmax()
        lightest = unconstrained.idxmin()
        bounds = WeightBounds(
            min_weight=0.05,
            max_weight=0.60,
            asset_min_weights={lightest: 0.30},
            asset_max_weights={heaviest: 0.10},
        )
        weights, _ = calculate_hrp(self.returns, bounds)
        self.assertAlmostEqual(weights.sum(), 1.0)
        self.assertLessEqual(weights[heaviest], 0.10 + 1e-9)
        self.assertGreaterEqual(weights[lightest], 0.30 - 1e-9)
        self.assertTrue((weights >= 0.05 - 1e-9).all())
        self.assertTrue((weights <= 0.60 + 1e-9).all())

    def test_infeasible_bounds_raise_clear_errors(self) -> None:
        cases = {
            "suma de los pesos mínimos": WeightBounds(min_weight=0.30),
            "suma de los pesos máximos": WeightBounds(max_weight=0.20),
            "no puede superar al máximo en: AAPL": WeightBounds(
                asset_min_weights={"AAPL": 0.5}, asset_max_weights={"AAPL": 0.4}
            ),
            "entre 0 % y 100 %": WeightBounds(asset_max_weights={"MSFT": 1.5}),
            "fuera del portafolio: TSLA": WeightBounds(asset_min_weights={"TSLA": 0.1}),
        }
        for message, bounds in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    calculate_hrp(self.returns, bounds)

    def test_sortino_is_annualized_with_downside_below_zero(self) -> None:
        result = optimize_hrp(self.returns)
        portfolio = self.returns.dot(result.weights[self.returns.columns])
        downside = np.sqrt(
            (np.minimum(portfolio, 0) ** 2).sum() / (len(portfolio) - 1)
        )
        expected = portfolio.mean() / downside * np.sqrt(252)
        self.assertAlmostEqual(result.sortino_ratio, expected)

    def test_risk_contributions_add_up_to_one(self) -> None:
        weights, _ = calculate_hrp(self.returns)
        shares = calculate_risk_contributions(self.returns, weights)
        self.assertAlmostEqual(shares.sum(), 1.0)
        covariance = self.returns.cov().loc[weights.index, weights.index]
        variance = weights @ covariance @ weights
        expected = weights * (covariance @ weights) / variance
        pd.testing.assert_series_equal(shares, expected, check_names=False)
        _, summary = calculate_hrp_contributions(self.returns, weights)
        self.assertAlmostEqual(summary["Contribución al riesgo"].sum(), 1.0)

    def test_fitted_clustering_is_exposed(self) -> None:
        result = optimize_hrp(self.returns)
        self.assertIsNotNone(result.clustering)
        linkage_matrix = result.clustering.linkage_matrix_
        self.assertEqual(linkage_matrix.shape, (len(TICKERS) - 1, 4))
        corr = self.returns.corr()
        distance = np.sqrt(np.clip((1 - corr) / 2, 0, 1))
        expected = linkage(squareform(distance, checks=False), "single")
        np.testing.assert_allclose(linkage_matrix[:, 2], expected[:, 2], atol=1e-9)
        self.assertEqual(
            list(result.ordered_correlation.index),
            [self.returns.columns[i] for i in leaves_list(linkage_matrix)],
        )


def _cluster_variance(covariance: pd.DataFrame, assets: list[str]) -> float:
    sub_covariance = covariance.loc[assets, assets]
    inverse_variance = 1 / np.diag(sub_covariance)
    weights = inverse_variance / inverse_variance.sum()
    return float(weights @ sub_covariance.to_numpy() @ weights)


if __name__ == "__main__":
    unittest.main()
