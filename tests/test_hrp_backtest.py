"""Tests for the walk-forward HRP backtest (vectorbt) with synthetic prices."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics.statistics import TRADING_DAYS
from optimizacion_portafolios.models import hrp_backtest
from optimizacion_portafolios.models.hrp import WeightBounds
from optimizacion_portafolios.models.hrp_backtest import (
    BUY_AND_HOLD,
    DRIFT,
    STRATEGY_KEYS,
    VOLATILITY,
    BacktestConfig,
    WalkForwardWeights,
    _rebalance_schedule,
    backtest_start,
    cached_backtest,
    clear_backtest_cache,
    run_backtest,
)
from support import make_prices

TICKERS = ("AAPL", "MSFT", "XLV", "GLD")


class FixedWeights(WalkForwardWeights):
    """Estimator stub with constant targets (keeps the window checks)."""

    def __init__(self, returns, lookback, weights):
        super().__init__(returns, lookback, None)
        self.fixed = np.asarray(weights, dtype=float)
        self.calls: list[int] = []

    def weights(self, position):
        self.window(position)
        self.calls.append(position)
        return self.fixed


def _schedule_positions(strategy, prices, start, estimator, config):
    return [
        position
        for position, _ in _rebalance_schedule(strategy, prices, start, estimator, config)
    ]


class ScheduleTests(unittest.TestCase):
    def test_calendar_triggers_fire_on_first_trading_day_of_each_period(self) -> None:
        prices = make_prices(TICKERS, rows=700)
        lookback = 60
        start = backtest_start(prices.index, lookback)
        estimator = FixedWeights(prices.pct_change(), lookback, [0.25] * 4)
        index = prices.index
        expected_keys = {
            "monthly": lambda day: (day.year, day.month),
            "quarterly": lambda day: (day.year, (day.month - 1) // 3),
            "semiannual": lambda day: (day.year, (day.month - 1) // 6),
            "annual": lambda day: day.year,
        }
        for strategy, key in expected_keys.items():
            with self.subTest(strategy=strategy):
                positions = _schedule_positions(
                    strategy, prices, start, estimator, BacktestConfig(lookback=lookback)
                )
                expected = [start] + [
                    i for i in range(start + 1, len(index)) if key(index[i]) != key(index[i - 1])
                ]
                self.assertEqual(positions, expected)
        # Quarter starts fall in January, April, July and October only.
        quarterly = _schedule_positions(
            "quarterly", prices, start, estimator, BacktestConfig(lookback=lookback)
        )[1:]
        self.assertTrue(all(index[i].month in (1, 4, 7, 10) for i in quarterly))

    def test_buy_and_hold_never_rebalances(self) -> None:
        prices = make_prices(TICKERS, rows=400)
        estimator = FixedWeights(prices.pct_change(), 60, [0.25] * 4)
        start = backtest_start(prices.index, 60)
        positions = _schedule_positions(
            BUY_AND_HOLD, prices, start, estimator, BacktestConfig(lookback=60)
        )
        self.assertEqual(positions, [start])

    def test_drift_trigger_fires_the_day_after_the_threshold_is_crossed(self) -> None:
        # A grows 1 % a day, B is flat: from 50/50 the weight of A reaches
        # 55 % (5 pp drift) after ceil(ln(1.2222…)/ln(1.01)) = 21 days.
        rows, lookback = 200, 30
        index = pd.bdate_range("2024-01-02", periods=rows)
        prices = pd.DataFrame(
            {"A": 100 * 1.01 ** np.arange(rows), "B": np.full(rows, 50.0)}, index=index
        )
        start = backtest_start(index, lookback)
        estimator = FixedWeights(prices.pct_change(), lookback, [0.5, 0.5])
        positions = _schedule_positions(
            DRIFT, prices, start, estimator, BacktestConfig(lookback=lookback, drift_threshold=0.05)
        )
        days = int(np.ceil(np.log(0.55 / 0.45) / np.log(1.01)))
        self.assertEqual(days, 21)
        expected = list(range(start, rows, days + 1))
        self.assertEqual(positions, expected)

    def test_volatility_trigger_fires_on_regime_changes(self) -> None:
        # Identical assets (weights never drift): calm ±0.5 % days, a turbulent
        # ±3 % block and calm again; fixed 20 % threshold with a 10-day window.
        rows, lookback, window = 260, 40, 10
        sign = np.where(np.arange(rows) % 2 == 0, 1.0, -1.0)
        size = np.full(rows, 0.005)
        size[120:170] = 0.03
        daily = sign * size
        daily[0] = 0.0
        close = 100 * np.cumprod(1 + daily)
        index = pd.bdate_range("2024-01-02", periods=rows)
        prices = pd.DataFrame({"A": close, "B": close}, index=index)
        config = BacktestConfig(lookback=lookback, vol_window=window, vol_threshold=0.20)
        start = backtest_start(index, lookback)
        estimator = FixedWeights(prices.pct_change(), lookback, [0.5, 0.5])
        positions = _schedule_positions(VOLATILITY, prices, start, estimator, config)

        vol = prices["A"].pct_change().rolling(window).std() * np.sqrt(TRADING_DAYS)
        band = hrp_backtest.VOL_HYSTERESIS
        up = next(i for i in range(start, rows) if vol.iloc[i] > 0.20 * (1 + band))
        down = next(i for i in range(up, rows) if vol.iloc[i] < 0.20 * (1 - band))
        self.assertEqual(positions, [start, up + 1, down + 1])
        self.assertTrue(120 <= up < 130 and 170 <= down < 180)

    def test_auto_volatility_threshold_waits_for_history(self) -> None:
        prices = make_prices(TICKERS, rows=400)
        lookback, window = 60, 30
        start = backtest_start(prices.index, lookback)
        estimator = FixedWeights(prices.pct_change(), lookback, [0.25] * 4)
        positions = _schedule_positions(
            VOLATILITY, prices, start, estimator,
            BacktestConfig(lookback=lookback, vol_window=window, vol_threshold=None),
        )
        # The expanding median needs ``window`` volatility observations first.
        self.assertTrue(all(p > start + window for p in positions[1:]))


class NoLookAheadTests(unittest.TestCase):
    def test_estimation_window_ends_the_day_before_execution(self) -> None:
        prices = make_prices(TICKERS, rows=300)
        estimator = WalkForwardWeights(prices.pct_change(), 60, None)
        window = estimator.window(200)
        self.assertEqual(len(window), 60)
        self.assertEqual(window.index[-1], prices.index[199])
        self.assertFalse(window.isna().to_numpy().any())
        with self.assertRaises(ValueError):
            estimator.window(60)  # would include the NaN first return

    def test_future_prices_do_not_change_past_decisions(self) -> None:
        prices = make_prices(TICKERS, rows=520)
        config = BacktestConfig(lookback=60, vol_window=20, drift_threshold=0.03)
        cut = 330
        shocked = prices.copy()
        rng = np.random.default_rng(7)
        shocked.iloc[cut:] *= np.cumprod(1 + rng.normal(0, 0.05, (len(prices) - cut, 4)), axis=0)
        base = run_backtest(prices, config)
        other = run_backtest(shocked, config)
        cut_date = prices.index[cut]
        for label in base.labels.values():
            with self.subTest(strategy=label):
                before = base.target_weights[label].loc[:cut_date]
                pd.testing.assert_frame_equal(before, other.target_weights[label].loc[:cut_date])
                self.assertEqual(
                    [d for d in base.rebalance_dates[label] if d <= cut_date],
                    [d for d in other.rebalance_dates[label] if d <= cut_date],
                )
                pd.testing.assert_series_equal(
                    base.values[label].loc[: prices.index[cut - 1]],
                    other.values[label].loc[: prices.index[cut - 1]],
                )
        # And the shock does change what happens afterwards.
        self.assertFalse(base.values.iloc[-1].equals(other.values.iloc[-1]))


class RunBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prices = make_prices(TICKERS, rows=420)

    def test_all_strategies_share_start_and_report_metrics(self) -> None:
        result = run_backtest(self.prices, BacktestConfig(lookback=60))
        self.assertEqual(list(result.labels), list(STRATEGY_KEYS))
        self.assertEqual(list(result.values.columns), list(result.labels.values()))
        self.assertEqual(result.start, self.prices.index[61])
        for column in (
            "Retorno total", "CAGR", "Volatilidad anualizada", "Ratio de Sharpe",
            "Ratio de Sortino", "Máxima caída", "Rebalanceos", "Comisiones",
            "Costos totales",
        ):
            self.assertIn(column, result.metrics.columns)
        self.assertEqual(result.metrics.loc["Buy & hold", "Rebalanceos"], 0)
        self.assertEqual(
            result.metrics.loc["Mensual", "Rebalanceos"],
            len(result.rebalance_dates["Mensual"]),
        )
        self.assertTrue((result.metrics["Máxima caída"] <= 0).all())
        self.assertTrue((result.metrics["Comisiones"] > 0).all())
        initial = {
            label: weights.iloc[0] for label, weights in result.target_weights.items()
        }
        for weights in initial.values():
            pd.testing.assert_series_equal(weights, initial["Buy & hold"])
            self.assertAlmostEqual(weights.sum(), 1.0)

    def test_frictionless_buy_and_hold_matches_manual_value(self) -> None:
        config = BacktestConfig(lookback=60, fees=0.0, slippage=0.0, init_cash=1000.0)
        result = run_backtest(self.prices, config, strategies=(BUY_AND_HOLD,))
        weights = result.target_weights["Buy & hold"].iloc[0]
        window = self.prices.loc[result.start :]
        expected = 1000.0 * (window / window.iloc[0]).dot(weights)
        np.testing.assert_allclose(result.values["Buy & hold"].to_numpy(), expected.to_numpy(), rtol=1e-9)
        self.assertEqual(result.metrics.loc["Buy & hold", "Costos totales"], 0.0)

    def test_more_rebalancing_costs_more(self) -> None:
        result = run_backtest(self.prices, BacktestConfig(lookback=60, fees=0.002))
        costs = result.metrics["Costos totales"]
        self.assertGreater(costs["Mensual"], costs["Anual"])
        self.assertGreater(costs["Anual"], costs["Buy & hold"])

    def test_weight_bounds_apply_to_every_rebalance(self) -> None:
        bounds = WeightBounds(max_weight=0.3)
        result = run_backtest(self.prices, BacktestConfig(lookback=60), bounds, strategies=("monthly",))
        self.assertTrue((result.target_weights["Mensual"] <= 0.3 + 1e-6).all().all())

    def test_start_date_and_insufficient_history(self) -> None:
        start_date = self.prices.index[200]
        result = run_backtest(self.prices, BacktestConfig(lookback=60), start_date=start_date,
                              strategies=(BUY_AND_HOLD,))
        self.assertEqual(result.start, start_date)
        with self.assertRaisesRegex(ValueError, "No hay datos suficientes"):
            run_backtest(self.prices, BacktestConfig(lookback=400))

    def test_invalid_config_is_rejected(self) -> None:
        for kwargs in ({"lookback": 5}, {"drift_threshold": 0}, {"fees": -0.1}, {"init_cash": 0}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                BacktestConfig(**kwargs)


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_backtest_cache()
        self.addCleanup(clear_backtest_cache)

    def test_same_inputs_reuse_the_result(self) -> None:
        prices = make_prices(TICKERS, rows=300)
        config = BacktestConfig(lookback=60)
        with patch.object(hrp_backtest, "run_backtest", wraps=hrp_backtest.run_backtest) as run:
            first, cached_first = cached_backtest(prices, config, strategies=(BUY_AND_HOLD,))
            second, cached_second = cached_backtest(prices.copy(), config, strategies=(BUY_AND_HOLD,))
            self.assertFalse(cached_first)
            self.assertTrue(cached_second)
            self.assertIs(first, second)
            self.assertEqual(run.call_count, 1)
            _, cached_third = cached_backtest(
                prices, BacktestConfig(lookback=60, fees=0.002), strategies=(BUY_AND_HOLD,)
            )
            self.assertFalse(cached_third)
            changed = prices.copy()
            changed.iloc[-1, 0] *= 1.01
            _, cached_fourth = cached_backtest(changed, config, strategies=(BUY_AND_HOLD,))
            self.assertFalse(cached_fourth)
            self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
