"""Tests for interface-independent analytics."""

import unittest
from datetime import date

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics.statistics import (
    calculate_expected_returns,
    calculate_statistics,
    date_years_ago,
)
from optimizacion_portafolios.analytics.volatility import (
    calculate_historical_volatility,
)
from support import make_prices


class StatisticsTests(unittest.TestCase):
    def test_date_years_ago_handles_leap_day(self) -> None:
        self.assertEqual(date_years_ago(date(2024, 2, 29)), date(2023, 2, 28))
        self.assertEqual(date_years_ago(date(2026, 10, 9), 2), date(2024, 10, 9))

    def test_statistics_report_total_return_and_drawdown(self) -> None:
        prices = pd.DataFrame(
            {"A": [100.0, 120.0, 90.0, 110.0]},
            index=pd.bdate_range("2025-01-01", periods=4),
        )
        statistics = calculate_statistics(prices)
        self.assertAlmostEqual(statistics.loc["A", "Retorno total"], 0.10)
        self.assertAlmostEqual(statistics.loc["A", "Máxima caída"], -0.25)
        self.assertAlmostEqual(statistics.loc["A", "Último precio"], 110.0)

    def test_expected_returns_compound_historical_and_ema_means(self) -> None:
        prices = make_prices(("A", "B"), rows=120)
        prices["EMPTY"] = np.nan
        prices.iloc[:5, 1] = np.nan  # later listing: shorter history for B
        expected = calculate_expected_returns(prices)
        self.assertEqual(list(expected.index), ["A", "B"])
        for ticker in ("A", "B"):
            series = prices[ticker].dropna()
            returns = series.pct_change().dropna()
            cagr = (series.iloc[-1] / series.iloc[0]) ** (252 / len(returns)) - 1
            self.assertAlmostEqual(
                expected.loc[ticker, "Retorno histórico esperado"], cagr
            )
        returns = prices["A"].pct_change().dropna()
        weights = (1 - 2 / 501) ** np.arange(len(returns))[::-1]
        ema = (returns * weights).sum() / weights.sum()
        self.assertAlmostEqual(
            expected.loc["A", "Retorno esperado EMA"], (1 + ema) ** 252 - 1
        )

    def test_expected_returns_of_constant_growth(self) -> None:
        daily = 0.001
        prices = pd.DataFrame(
            {"A": 100 * (1 + daily) ** np.arange(50)},
            index=pd.bdate_range("2025-01-01", periods=50),
        )
        expected = calculate_expected_returns(prices).loc["A"]
        self.assertAlmostEqual(expected["Retorno histórico esperado"], 1.001**252 - 1)
        self.assertAlmostEqual(expected["Retorno esperado EMA"], 1.001**252 - 1)


class VolatilityTests(unittest.TestCase):
    def test_monthly_volatility_excludes_partial_months(self) -> None:
        prices = make_prices(("A",), rows=90)  # 2024-01-02 .. mid-May 2024
        returns = prices.pct_change(fill_method=None).dropna()
        period_vol, annualized_vol = calculate_historical_volatility(
            returns, "Mensual", prices.index.min(), prices.index.max()
        )
        months = list(annualized_vol.index.strftime("%Y-%m"))
        self.assertNotIn("2024-01", months)  # starts on Jan 2, not Jan 1
        self.assertNotIn("2024-05", months)  # ends before May 31
        self.assertEqual(months, ["2024-02", "2024-03", "2024-04"])
        self.assertTrue((period_vol["A"] < annualized_vol["A"]).all())

    def test_annualized_volatility_scales_daily_std_by_sqrt_252(self) -> None:
        prices = make_prices(("A",), rows=90)
        returns = prices.pct_change(fill_method=None).dropna()
        _, annualized_vol = calculate_historical_volatility(
            returns, "Mensual", prices.index.min(), prices.index.max()
        )
        february = returns.loc["2024-02", "A"]
        self.assertAlmostEqual(
            annualized_vol.loc["2024-02-29", "A"],
            february.std(ddof=1) * np.sqrt(252),
        )


if __name__ == "__main__":
    unittest.main()


class ReturnsHelpersTests(unittest.TestCase):
    """analytics.returns: shared price preparation and returns-matrix series."""

    def setUp(self):
        from optimizacion_portafolios.analytics import returns

        self.returns = returns
        index = pd.date_range("2024-01-05", periods=4, freq="D")  # Fri..Mon
        self.raw = pd.DataFrame(
            {"A": [10.0, None, None, 11.0], "BTC": [100.0, 101.0, 102.0, 103.0], "X": [None] * 4},
            index=index,
        )

    def test_prepare_prices_drops_empty_columns_and_aligns(self):
        raw, aligned = self.returns.prepare_prices(self.raw)
        self.assertEqual(list(raw.columns), ["A", "BTC"])
        self.assertEqual(aligned["A"].tolist(), [10.0, 10.0, 10.0, 11.0])

    def test_returns_matrix_series_keeps_each_asset_calendar(self):
        raw, _ = self.returns.prepare_prices(self.raw)
        series = self.returns.returns_matrix_series(
            raw, ["BTC", "A"], portfolio=pd.Series([0.01]), portfolio_key="P",
            benchmark="SPY", benchmark_prices=pd.Series([1.0, 1.1]),
        )
        self.assertEqual(list(series), ["P", "SPY", "BTC", "A"])
        self.assertEqual(len(series["A"]), 1)  # only its own two quotes
        self.assertEqual(len(series["BTC"]), 3)
        self.assertNotIn("A", self.returns.returns_matrix_series(raw, ["A"], benchmark="A", benchmark_prices=raw["A"]).keys() - {"A"})

    def test_weighted_portfolio_returns(self):
        prices = pd.DataFrame({"A": [1.0, 1.1], "B": [1.0, 0.9]})
        result = self.returns.weighted_portfolio_returns(prices, {"A": 0.5, "B": 0.5})
        self.assertAlmostEqual(result.iloc[0], 0.0)
