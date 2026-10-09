"""Tests for interface-independent analytics."""

import unittest
from datetime import date

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics.statistics import (
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
