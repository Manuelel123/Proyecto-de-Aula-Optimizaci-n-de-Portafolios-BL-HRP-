"""F3: tracking metrics with hand-computed values, snapshots and the CLI."""

import math
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics import tracking as t
from optimizacion_portafolios.analytics.tracking import Segment
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.repositories import HoldingData
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.portfolios import services
from test_tracking import _prices


def _series(values, start="2026-01-05"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


class ReturnMetricsTests(unittest.TestCase):
    def test_cumulative_and_cagr(self):
        self.assertAlmostEqual(t.cumulative_return(_series([100, 110, 99])), -0.01)
        two_years = pd.Series([100.0, 121.0], index=pd.to_datetime(["2024-01-01", "2025-12-31"]))
        years = (two_years.index[1] - two_years.index[0]).days / 365.25
        self.assertAlmostEqual(t.cagr(two_years), 1.21 ** (1 / years) - 1)

    def test_period_returns_use_the_close_before_the_period(self):
        values = pd.Series(
            [100.0, 104.0, 110.0, 99.0],
            index=pd.to_datetime(["2025-12-30", "2025-12-31", "2026-01-30", "2026-02-02"]),
        )
        periods = t.period_returns(values)
        self.assertAlmostEqual(periods["mtd"], 99 / 110 - 1)   # since Jan 30 close
        self.assertAlmostEqual(periods["ytd"], 99 / 104 - 1)   # since Dec 31 close
        self.assertIsNone(periods["one_year"])  # less than a year of history
        longer = pd.concat([pd.Series([90.0], index=pd.to_datetime(["2025-01-15"])), values])
        self.assertAlmostEqual(t.period_returns(longer)["one_year"], 99 / 90 - 1)

    def test_return_contributions_add_up_to_the_cumulative_return(self):
        prices = pd.DataFrame({"A": [10.0, 11.0, 12.1], "B": [20.0, 19.0, 19.0]}, index=pd.bdate_range("2026-01-05", periods=3))
        holdings = t.holding_values(prices, [Segment(prices.index[0], {"A": 50.0, "B": 25.0})])
        contributions = t.return_contributions(holdings)
        self.assertAlmostEqual(contributions["A"], (605 - 500) / 1000)
        self.assertAlmostEqual(contributions["B"], (475 - 500) / 1000)
        self.assertAlmostEqual(contributions.sum(), t.cumulative_return(holdings.sum(axis=1)))


class RiskMetricsTests(unittest.TestCase):
    def test_drawdown_current_max_and_duration(self):
        values = pd.Series(
            [100.0, 120.0, 90.0, 110.0, 130.0, 100.0],
            index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-12", "2026-01-20", "2026-01-26"]),
        )
        stats = t.drawdown_stats(values)
        self.assertAlmostEqual(stats["max"], 90 / 120 - 1)
        self.assertAlmostEqual(stats["current"], 100 / 130 - 1)
        self.assertEqual(stats["max_duration_days"], 10)  # Jan 2 peak -> Jan 12 still below
        self.assertEqual(stats["current_duration_days"], 6)

    def test_historical_var_and_cvar(self):
        returns = _series([-0.10 + 0.01 * i for i in range(20)])
        self.assertAlmostEqual(t.historical_var(returns), 0.0905)
        self.assertAlmostEqual(t.historical_cvar(returns), 0.10)
        self.assertIsNone(t.historical_var(returns.iloc[:10]))

    def test_semideviation(self):
        returns = _series([0.01, -0.02, 0.03, -0.04])
        self.assertAlmostEqual(t.semideviation(returns), math.sqrt((0.0004 + 0.0016) / 4) * math.sqrt(252))

    def test_volatility_matches_the_sample_formula(self):
        returns = _series([0.01, -0.02, 0.03, -0.04])
        self.assertAlmostEqual(t.annualized_volatility(returns), returns.std(ddof=1) * math.sqrt(252))
        rolling = t.rolling_volatility(returns, 2)
        self.assertEqual(len(rolling), 3)

    def test_risk_contributions_of_equal_independent_assets(self):
        rng = np.random.default_rng(3)
        first = rng.normal(0, 0.01, 2000)
        returns = pd.DataFrame({"A": first, "B": rng.permutation(first)})
        shares = t.risk_contributions(returns, pd.Series({"A": 0.5, "B": 0.5}))
        self.assertAlmostEqual(shares.sum(), 1.0)
        self.assertAlmostEqual(shares["A"], 0.5, delta=0.03)


class RatioTests(unittest.TestCase):
    def test_sharpe_and_sortino_follow_quantstats_conventions(self):
        returns = _series([0.01, -0.005, 0.02, -0.01, 0.015])
        expected = returns.mean() / returns.std(ddof=1) * math.sqrt(252)
        self.assertAlmostEqual(t.sharpe_ratio(returns), expected)
        downside = math.sqrt((returns.clip(upper=0) ** 2).mean())
        self.assertAlmostEqual(t.sortino_ratio(returns), returns.mean() / downside * math.sqrt(252))
        self.assertIsNone(t.sortino_ratio(_series([0.01, 0.02, 0.03])))

    def test_calmar_and_negative_streak(self):
        values = pd.Series([100.0, 80.0, 121.0], index=pd.to_datetime(["2024-01-01", "2024-06-01", "2025-12-31"]))
        self.assertAlmostEqual(t.calmar_ratio(values), t.cagr(values) / 0.2)
        self.assertEqual(t.negative_streak(_series([0.5, -0.1, 0.2, -0.3, -0.2])), 2)

    def test_benchmark_metrics_of_a_levered_copy(self):
        benchmark = _series([0.01, -0.02, 0.015, -0.005, 0.01])
        metrics = t.benchmark_metrics(2 * benchmark, benchmark)
        self.assertAlmostEqual(metrics["beta"], 2.0)
        self.assertAlmostEqual(metrics["alpha"], 0.0)
        self.assertAlmostEqual(metrics["tracking_error"], benchmark.std(ddof=1) * math.sqrt(252))
        self.assertAlmostEqual(metrics["upside_capture"], 2.0)
        self.assertAlmostEqual(metrics["downside_capture"], 2.0)


class StructureTests(unittest.TestCase):
    def test_concentration(self):
        self.assertAlmostEqual(t.herfindahl(pd.Series({"A": 0.5, "B": 0.5})), 0.5)
        self.assertAlmostEqual(t.effective_assets(pd.Series({"A": 0.5, "B": 0.25, "C": 0.25})), 1 / 0.375)

    def test_rolling_average_correlation(self):
        base = _series(np.random.default_rng(5).normal(0, 0.01, 80))
        correlation = t.rolling_average_correlation(pd.DataFrame({"A": base, "B": base * 2, "C": base}), 63)
        self.assertEqual(len(correlation), 80 - 62)
        self.assertAlmostEqual(correlation.iloc[-1], 1.0)

    def test_tracking_metrics_bundle(self):
        prices = make_tracking_prices()
        holdings = t.holding_values(prices, [Segment(prices.index[0], {"A": 50.0, "B": 25.0})])
        total = holdings.sum(axis=1)
        result = t.tracking_metrics(total, holdings, prices, prices["A"], {"A": 0.5, "B": 0.5}, turnover=0.2)
        scalars = result["scalars"]
        self.assertAlmostEqual(scalars["cumulative_return"], total.iloc[-1] / total.iloc[0] - 1)
        self.assertEqual(scalars["observations"], len(total) - 1)
        self.assertEqual(scalars["turnover"], 0.2)
        self.assertIsNotNone(scalars["rolling_sharpe_126"])
        self.assertIsNotNone(scalars["beta"])
        self.assertAlmostEqual(result["risk_contributions"].sum(), 1.0)


def make_tracking_prices(rows: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    index = pd.bdate_range("2025-01-02", periods=rows)
    return pd.DataFrame(
        {"A": 10 * np.cumprod(1 + rng.normal(0.0005, 0.01, rows)), "B": 20 * np.cumprod(1 + rng.normal(0.0003, 0.012, rows))},
        index=index,
    )


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "x"})
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.today = date.today()
        self.portfolio = repositories.create_portfolio(
            "user-a", name="P", model="hrp", benchmark="SPY", initial_capital=10_000,
            tracking_start=self.today - timedelta(days=60),
            holdings=[HoldingData("AAPL", 0.5, 30.0, 150.0), HoldingData("MSFT", 0.5, 15.0, 300.0)],
        )

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    def test_snapshots_backfill_and_only_refresh_the_last_day(self, _download):
        analysis = services.analyze(self.portfolio, self.today)
        written = services.record_snapshots(self.portfolio, analysis)
        days = len(analysis["total"])
        self.assertEqual(written, days)
        snapshots = repositories.latest_snapshots(self.portfolio, 500)
        self.assertEqual(len(snapshots), days)
        self.assertIsNotNone(snapshots[0].metrics)          # latest day
        self.assertIsNone(snapshots[-1].metrics)            # backfilled day
        self.assertAlmostEqual(sum(snapshots[0].weights.values()), 1.0)

        oldest = snapshots[-1]
        oldest.value = 1.0  # history must survive later provider revisions
        self.assertEqual(services.record_snapshots(self.portfolio, analysis), 1)
        self.assertEqual(repositories.latest_snapshots(self.portfolio, 500)[-1].value, 1.0)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    def test_cli_command_snapshots_active_portfolios(self, _download):
        result = self.app.test_cli_runner().invoke(args=["portfolios", "snapshot"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("#%d P:" % self.portfolio.id, result.output)
        self.assertGreater(len(repositories.latest_snapshots(self.portfolio, 500)), 0)

    def test_cli_reports_failures(self):
        with patch.object(services, "download_prices", side_effect=lambda t, s, e: pd.DataFrame()):
            result = self.app.test_cli_runner().invoke(args=["portfolios", "snapshot"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("ERROR", result.output)


if __name__ == "__main__":
    unittest.main()
