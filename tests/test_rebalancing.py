"""F5: rebalancing math, previews and the optimize → fix → track → rebalance path."""

import re
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from optimizacion_portafolios.analytics import tracking
from optimizacion_portafolios.models.hrp import WeightBounds
from optimizacion_portafolios.models.rebalancing import (
    apply_bounds,
    plan_change,
    with_assets,
    without_assets,
)
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.repositories import HoldingData
from optimizacion_portafolios.web.portfolios import services
from test_tracking import _prices
from test_web_accounts import AccountTestCase


class WeightChangeTests(unittest.TestCase):
    def test_bounds_clip_and_redistribute_proportionally(self):
        weights = apply_bounds({"A": 0.7, "B": 0.2, "C": 0.1}, WeightBounds(0.0, 0.5))
        self.assertAlmostEqual(weights["A"], 0.5)
        self.assertAlmostEqual(weights["B"], 0.2 + 0.2 * 2 / 3)
        self.assertAlmostEqual(weights["C"], 0.1 + 0.2 / 3)
        self.assertAlmostEqual(weights.sum(), 1.0)

    def test_minimums_and_per_asset_limits(self):
        bounds = WeightBounds(0.1, 1.0, asset_max_weights={"A": 0.4})
        weights = apply_bounds({"A": 0.9, "B": 0.1, "C": 0.0}, bounds)
        self.assertAlmostEqual(weights["A"], 0.4)
        self.assertGreaterEqual(weights["C"], 0.1 - 1e-12)
        self.assertAlmostEqual(weights.sum(), 1.0)

    def test_infeasible_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            apply_bounds({"A": 0.5, "B": 0.5}, WeightBounds(0.0, 0.4))

    def test_removal_and_addition(self):
        removed = without_assets({"A": 0.5, "B": 0.3, "C": 0.2}, ["A"])
        self.assertAlmostEqual(removed["B"], 0.6)
        self.assertAlmostEqual(removed["C"], 0.4)
        added = with_assets({"A": 0.6, "B": 0.4}, {"N": 0.2})
        self.assertAlmostEqual(added["A"], 0.48)
        self.assertAlmostEqual(added["N"], 0.2)
        with self.assertRaises(ValueError):
            with_assets({"A": 1.0}, {"N": 1.0})


class PlanTests(unittest.TestCase):
    def test_rebalance_costs_turnover_and_units(self):
        # Drifted to 70/30 (value 1000); back to 50/50 with 10 bps.
        plan = plan_change({"A": 70.0, "B": 30.0}, {"A": 10.0, "B": 10.0}, {"A": 0.5, "B": 0.5}, 10)
        self.assertAlmostEqual(plan.value_before, 1000.0)
        traded = sum(trade.amount for trade in plan.trades)
        self.assertAlmostEqual(plan.cost, traded * 0.001)  # fixed point holds
        self.assertAlmostEqual(plan.value_after, 1000.0 - plan.cost)
        self.assertAlmostEqual(plan.turnover, traded / 2000)
        self.assertAlmostEqual(plan.turnover, 0.2, places=3)
        self.assertAlmostEqual(plan.units_after["A"], plan.value_after * 0.5 / 10)
        sides = {trade.ticker: trade.side for trade in plan.trades}
        self.assertEqual(sides, {"A": "sell", "B": "buy"})

    def test_no_trades_when_already_on_target(self):
        plan = plan_change({"A": 50.0, "B": 50.0}, {"A": 10.0, "B": 10.0}, {"A": 0.5, "B": 0.5}, 10)
        self.assertEqual((plan.trades, plan.cost, plan.turnover), ([], 0.0, 0.0))

    def test_removed_asset_is_sold_entirely(self):
        plan = plan_change({"A": 50.0, "B": 50.0}, {"A": 10.0, "B": 10.0}, {"B": 1.0}, 0)
        self.assertNotIn("A", plan.units_after)
        sale = next(trade for trade in plan.trades if trade.ticker == "A")
        self.assertEqual((sale.side, sale.units), ("sell", 50.0))

    def test_value_is_continuous_across_the_change(self):
        prices = pd.DataFrame({"A": [10.0, 12.0, 12.0], "B": [10.0, 9.0, 10.0]}, index=pd.bdate_range("2026-01-05", periods=3))
        first = {"A": 50.0, "B": 50.0}
        day = prices.index[1]
        plan = plan_change(first, prices.loc[day].to_dict(), {"A": 0.5, "B": 0.5}, 10)
        values = tracking.portfolio_values(
            prices, [tracking.Segment(prices.index[0], first), tracking.Segment(day, plan.units_after)]
        )
        self.assertAlmostEqual(values[day], plan.value_before - plan.cost)


class ManagementFlowTests(AccountTestCase):
    def _snapshot(self):
        html = self.post(
            "/hrp", action="optimize", universe="Portafolio actual", benchmark="S&P 500 (SPY)",
            start_date="2024-01-01", tickers=["AAPL", "MSFT", "XLV"], custom_tickers="", backtest="",
            max_weight="60",
        ).data.decode()
        return re.search(r'name="snapshot" value="([^"]+)"', html).group(1)

    def _fix(self):
        response = self.post(
            "/portafolios/fijar", snapshot=self._snapshot(), step="confirm", name="Mi HRP",
            capital="100000", commission_bps="10", benchmark="SPY",
        )
        return int(response.location.rstrip("/").rsplit("/", 1)[-1])

    def _portfolio(self, portfolio_id):
        return repositories.get_portfolio(self.insforge.users["ana@example.com"]["id"], portfolio_id)

    def _confirm(self, portfolio_id, **form):
        html = self.post(f"/portafolios/{portfolio_id}/gestionar/vista-previa", **form).data.decode()
        self.assertIn("Vista previa", html)
        token = re.search(r'name="token" value="([^"]+)"', html).group(1)
        response = self.post(f"/portafolios/{portfolio_id}/gestionar/confirmar", token=token, note="prueba")
        self.assertEqual(response.status_code, 302)
        return html

    @patch("optimizacion_portafolios.web.portfolios.management.download_prices", side_effect=_prices)
    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=_prices)
    def test_optimize_fix_track_and_rebalance(self, *_downloads):
        self.sign_in()
        portfolio_id = self._fix()
        self.assertIn("Valor actual", self.client.get(f"/portafolios/{portfolio_id}").data.decode())

        # 1. Rebalance to the targets.
        html = self._confirm(portfolio_id, action="rebalance", rebalance_method="targets")
        self.assertIn("Pesos antes y después", html)
        # 2. Remove MSFT, weight redistributed proportionally.
        html = self._confirm(portfolio_id, action="remove", remove_method="proportional", remove="MSFT")
        self.assertIn("Venta", html)
        # 3. Add GLD at 20 %.
        self._confirm(portfolio_id, action="add", add_method="manual", add_tickers="gld", add_weight="20")
        # 4. Re-optimize with HRP on the current universe.
        self._confirm(portfolio_id, action="rebalance", rebalance_method="reoptimize")

        with self.app.app_context():
            portfolio = self._portfolio(portfolio_id)
            versions = portfolio.versions
            self.assertEqual([v.reason for v in versions], ["fix", "rebalance", "remove", "add", "reoptimize"])
            removal, addition, reoptimized = versions[2], versions[3], versions[4]
            self.assertNotIn("MSFT", removal.target_weights)
            self.assertGreater(removal.cost, 0)
            self.assertAlmostEqual(removal.value_after, removal.value_before - removal.cost)
            self.assertTrue(any(t.ticker == "MSFT" and t.side == "sell" for t in removal.transactions))
            self.assertAlmostEqual(addition.target_weights["GLD"], 0.2)
            self.assertEqual(addition.note, "prueba")
            self.assertLessEqual(max(reoptimized.target_weights.values()), 0.6 + 1e-9)  # max_weight bound
            self.assertAlmostEqual(sum(reoptimized.target_weights.values()), 1.0)
            # Tracking continues (no reset): same start, value net of every cost.
            analysis = services.analyze(portfolio)
            self.assertEqual(analysis["total"].index[0].date(), portfolio.tracking_start)
            self.assertAlmostEqual(analysis["total"].iloc[-1], reoptimized.value_after, places=4)

        versions_page = self.client.get(f"/portafolios/{portfolio_id}/versiones?a=1&b=4").data.decode()
        self.assertIn("Versión 1 frente a versión 4", versions_page)
        self.assertIn("eliminado", versions_page)
        self.assertIn("nuevo", versions_page)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=_prices)
    def test_unknown_ticker_is_rejected_before_any_change(self, _hrp, portfolio_download):
        self.sign_in()
        portfolio_id = self._fix()
        portfolio_download.side_effect = lambda tickers, s, e: _prices([t for t in tickers if t != "ZZZZ"], s, e)
        html = self.post(
            f"/portafolios/{portfolio_id}/gestionar/vista-previa",
            action="add", add_method="manual", add_tickers="ZZZZ", add_weight="10",
        ).data.decode()
        self.assertIn("No hay precios recientes para: ZZZZ", html)
        with self.app.app_context():
            self.assertEqual(len(self._portfolio(portfolio_id).versions), 1)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=_prices)
    def test_stale_or_forged_previews_are_rejected(self, *_downloads):
        self.sign_in()
        portfolio_id = self._fix()
        html = self.post(f"/portafolios/{portfolio_id}/gestionar/vista-previa", action="rebalance", rebalance_method="targets").data.decode()
        token = re.search(r'name="token" value="([^"]+)"', html).group(1)
        self.post(f"/portafolios/{portfolio_id}/gestionar/confirmar", token=token)
        again = self.post(f"/portafolios/{portfolio_id}/gestionar/confirmar", token=token)
        self.assertEqual(again.status_code, 302)
        self.post(f"/portafolios/{portfolio_id}/gestionar/confirmar", token="forged")
        with self.app.app_context():
            self.assertEqual(len(self._portfolio(portfolio_id).versions), 2)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=_prices)
    def test_archived_portfolios_keep_history_but_reject_changes(self, *_downloads):
        self.sign_in()
        portfolio_id = self._fix()
        self.post(f"/portafolios/{portfolio_id}/archivar")
        response = self.client.get(f"/portafolios/{portfolio_id}/gestionar")
        self.assertEqual(response.status_code, 302)
        self.assertIn("Archivado", self.client.get(f"/portafolios/{portfolio_id}").data.decode())
        with self.app.app_context():
            self.assertEqual(repositories.active_portfolios(), [])
            self.assertEqual(len(self._portfolio(portfolio_id).versions), 1)

    def test_management_pages_of_another_user_are_404(self):
        self.sign_in()
        with self.app.app_context():
            other = repositories.create_portfolio(
                "someone-else", name="Ajeno", model="hrp", benchmark="SPY", initial_capital=1,
                tracking_start=date.today() - timedelta(days=3), holdings=[HoldingData("A", 1.0, 1.0, 1.0)],
            ).id
        for path in ("gestionar", "versiones"):
            self.assertEqual(self.client.get(f"/portafolios/{other}/{path}").status_code, 404)
        self.assertEqual(self.post(f"/portafolios/{other}/gestionar/vista-previa", action="rebalance").status_code, 404)
        self.assertEqual(self.post(f"/portafolios/{other}/archivar").status_code, 404)


if __name__ == "__main__":
    unittest.main()
