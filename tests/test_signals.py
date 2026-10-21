"""F6: per-asset signals with vectorbt (suggestions that never change the portfolio)."""

import unittest
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

from optimizacion_portafolios.models.signals import SignalConfig, asset_state, entry_exit, run_signals
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.repositories import HoldingData
from test_tracking import _prices
from test_web_accounts import AccountTestCase

INDEX = pd.bdate_range("2025-01-01", periods=120)


class StateTests(unittest.TestCase):
    def test_trend_rule(self):
        prices = pd.DataFrame({"UP": np.linspace(10, 20, 120), "DOWN": np.linspace(20, 10, 120)}, index=INDEX)
        config = SignalConfig(fast_ma=5, slow_ma=20, vol_multiple=None, drawdown_exit=None, stop_loss=None)
        state = asset_state(prices, config)
        self.assertTrue(state["UP"].iloc[-1])
        self.assertFalse(state["DOWN"].iloc[-1])
        self.assertTrue(state.iloc[:19].all().all())  # no slow average yet: no signal

    def test_drawdown_rule(self):
        path = np.r_[np.linspace(10, 20, 60), np.linspace(20, 14, 60)]
        prices = pd.DataFrame({"A": path}, index=INDEX)
        config = SignalConfig(use_trend=False, vol_multiple=None, drawdown_exit=0.2, stop_loss=None)
        state = asset_state(prices, config)["A"]
        first_out = state[~state].index[0]
        self.assertLessEqual(prices.loc[first_out, "A"], 16.0 + 1e-9)  # 20 % below the 20 peak
        self.assertTrue(state.iloc[:60].all())

    def test_entries_and_exits_are_state_switches(self):
        state = pd.DataFrame({"A": [True, True, False, False, True]})
        entries, exits = entry_exit(state)
        self.assertEqual(list(entries["A"]), [True, False, False, False, True])
        self.assertEqual(list(exits["A"]), [False, False, True, False, False])

    def test_invalid_settings(self):
        with self.assertRaises(ValueError):
            SignalConfig(fast_ma=50, slow_ma=20)
        with self.assertRaises(ValueError):
            SignalConfig(stop_loss=-0.1)


class SimulationTests(unittest.TestCase):
    def test_stop_loss_beats_buy_and_hold_in_a_crash(self):
        path = np.r_[np.full(40, 100.0), np.linspace(100, 50, 80)]
        prices = pd.DataFrame({"A": path, "B": np.full(120, 50.0)}, index=INDEX)
        config = SignalConfig(use_trend=False, vol_multiple=None, drawdown_exit=None, stop_loss=0.10, trailing_stop=False, fees=0.0)
        result = run_signals(prices, {"A": 0.5, "B": 0.5}, 10_000, INDEX[0], config)
        self.assertEqual(list(result.values.columns), ["Señales", "Comprar y mantener"])
        self.assertAlmostEqual(result.values["Comprar y mantener"].iloc[-1], 7_500, delta=1)
        self.assertGreater(result.values["Señales"].iloc[-1], 9_400)  # A stopped near -10 %
        stopped = [trade for trade in result.trades if trade["ticker"] == "A"]
        self.assertEqual(stopped[0]["status"], "Cerrada")
        self.assertAlmostEqual(stopped[0]["return"], -0.10, delta=0.02)

    def test_signals_only_start_at_the_fixing_date(self):
        prices = pd.DataFrame({"A": np.linspace(10, 20, 120)}, index=INDEX)
        result = run_signals(prices, {"A": 1.0}, 1_000, INDEX[60], SignalConfig(fast_ma=5, slow_ma=20, stop_loss=None))
        self.assertEqual(result.values.index[0], INDEX[60])
        self.assertEqual(result.suggestions[0]["state"], "Mantener")


class SignalsPageTests(AccountTestCase):
    # Full access (fixing, alerts, management, signals, backtests): ana is a gestor.
    gestor_emails = "ana@example.com"

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    def test_page_simulates_without_changing_the_portfolio(self, _download):
        self.sign_in()
        with self.app.app_context():
            user_id = self.user_id_of()
            portfolio_id = repositories.create_portfolio(
                user_id, name="Seguido", model="hrp", benchmark="SPY", initial_capital=10_000,
                tracking_start=date.today() - timedelta(days=150),
                holdings=[HoldingData("AAPL", 0.5, 30.0, 150.0), HoldingData("MSFT", 0.5, 15.0, 300.0)],
            ).id
        html = self.client.get(f"/portafolios/{portfolio_id}/senales").data.decode()
        self.assertIn("Son sugerencias: nunca modifican el portafolio", html)
        self.assertIn('id="chart-signals_equity"', html)
        self.assertIn("Estado sugerido por activo", html)
        custom = self.client.get(
            f"/portafolios/{portfolio_id}/senales?submitted=1&fast_ma=10&slow_ma=40&vol_window=21&stop_loss=5"
        ).data.decode()
        self.assertIn('value="40"', custom)
        bad = self.client.get(f"/portafolios/{portfolio_id}/senales?submitted=1&use_trend=1&fast_ma=50&slow_ma=20").data.decode()
        self.assertIn("La media rápida debe ser menor que la lenta", bad)
        with self.app.app_context():
            self.assertEqual(len(repositories.get_portfolio(user_id, portfolio_id).versions), 1)

    def test_signals_of_another_user_are_404(self):
        self.sign_in()
        with self.app.app_context():
            other = repositories.create_portfolio(
                "someone-else", name="Ajeno", model="hrp", benchmark="SPY", initial_capital=1,
                tracking_start=date.today(), holdings=[HoldingData("A", 1.0, 1.0, 1.0)],
            ).id
        self.assertEqual(self.client.get(f"/portafolios/{other}/senales").status_code, 404)


if __name__ == "__main__":
    unittest.main()
