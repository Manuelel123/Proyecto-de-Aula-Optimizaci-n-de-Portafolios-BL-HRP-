"""F4: alert rules, event lifecycle and the alerts pages."""

import unittest
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics import alerts
from optimizacion_portafolios.analytics.tracking import stale_days
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.repositories import HoldingData
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.portfolios import alerting, services
from test_tracking import _prices
from test_web_accounts import AccountTestCase

DEFAULTS = {rule.kind: (rule.yellow, rule.red) for rule in alerts.RULES}


class RuleEvaluationTests(unittest.TestCase):
    def _levels(self, scalars, context=None):
        return {s.kind: s.level for s in alerts.evaluate(scalars, DEFAULTS, context)}

    def test_default_thresholds(self):
        scalars = {
            "drawdown": -0.12, "rolling_volatility_21": 0.30, "average_rolling_volatility_21": 0.15,
            "max_abs_drift": 0.04, "rolling_sharpe_126": -0.2, "negative_sharpe_days": 70,
            "max_risk_share": 0.45, "average_correlation": 0.5, "max_stale_days": 0,
        }
        self.assertEqual(
            self._levels(scalars),
            {
                "drawdown": "yellow",          # 12 % > 10 %
                "volatility": "yellow",        # 2.0× is not above 2.0×
                "weight_drift": "green",
                "negative_sharpe": "yellow",   # 70 > 63 days
                "risk_concentration": "red",   # 45 % > 40 %
                "correlation": "green",
                "stale_price": "green",
            },
        )
        self.assertEqual(self._levels({**scalars, "drawdown": -0.16})["drawdown"], "red")

    def test_missing_values_are_green(self):
        self.assertTrue(all(level == "green" for level in self._levels({}).values()))

    def test_messages_name_the_asset_and_threshold(self):
        signals = alerts.evaluate({"max_abs_drift": 0.07}, {"weight_drift": (0.05, 0.10)}, {"drift_ticker": "AAPL"})
        self.assertEqual(signals[0].message, "Desviación de pesos: 7.0 pp (AAPL) · umbral amarillo 5.0 pp")

    def test_overall_level(self):
        self.assertEqual(alerts.overall_level(["yellow", "red", "green"]), "red")
        self.assertEqual(alerts.overall_level([]), "green")

    def test_stale_days_counts_business_days_without_price(self):
        calendar = pd.bdate_range("2026-01-05", periods=6)
        raw = pd.DataFrame({"A": [1.0] * 6, "B": [1.0, 1.0, 1.0, np.nan, np.nan, np.nan]}, index=calendar)
        self.assertEqual(stale_days(raw, calendar).to_dict(), {"A": 0, "B": 3})


class EventLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "x"})
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.portfolio = repositories.create_portfolio(
            "user-a", name="P", model="hrp", benchmark="SPY", initial_capital=1000,
            tracking_start=date(2026, 1, 5), holdings=[HoldingData("A", 1.0, 10.0, 100.0)],
        )

    def _sync(self, level, day, value=0.1):
        signal = alerts.Signal("drawdown", level, value, f"Drawdown {level}")
        return repositories.sync_alerts(self.portfolio, [signal], day)

    def test_open_escalate_and_resolve(self):
        self.assertEqual(self._sync("yellow", date(2026, 2, 2))[0].level, "yellow")
        self.assertEqual(len(self._sync("yellow", date(2026, 2, 3), 0.11)), 1)  # same level: updated
        opened = self._sync("red", date(2026, 2, 4))
        self.assertEqual([e.level for e in opened], ["red"])
        self.assertEqual(self._sync("green", date(2026, 2, 5)), [])
        history = repositories.alert_history(self.portfolio)
        self.assertEqual(
            [(e.level, e.triggered_on.day, e.resolved_on.day) for e in history],
            [("red", 4, 5), ("yellow", 2, 4)],
        )

    def test_older_evaluations_do_not_reopen_alerts(self):
        self._sync("red", date(2026, 2, 4))
        self._sync("green", date(2026, 2, 3))
        self.assertEqual([e.level for e in repositories.open_alerts(self.portfolio)], ["red"])

    def test_disabled_rules_resolve_their_events(self):
        self._sync("yellow", date(2026, 2, 2))
        self.assertEqual(repositories.sync_alerts(self.portfolio, [], date(2026, 2, 3)), [])


def _crashing_prices(tickers, start, end):
    """Synthetic prices whose last 15 % of days fall 25 % (drawdown alert)."""
    frame = _prices(tickers, start, end)
    cut = int(len(frame) * 0.85)
    frame.iloc[cut:] = frame.iloc[cut:] * 0.75
    return frame


class AlertPagesTests(AccountTestCase):
    # Full access (fixing, alerts, management, signals, backtests): ana is a gestor.
    gestor_emails = "ana@example.com"

    def _portfolio(self):
        user_id = self.user_id_of()
        portfolio = repositories.create_portfolio(
            user_id, name="Seguido", model="hrp", benchmark="SPY", initial_capital=10_000,
            tracking_start=date.today() - timedelta(days=200),
            holdings=[HoldingData("AAPL", 0.5, 30.0, 150.0), HoldingData("MSFT", 0.5, 15.0, 300.0)],
        )
        return portfolio.id

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_crashing_prices)
    def test_dashboard_evaluates_rules_and_shows_the_traffic_light(self, _download):
        self.sign_in()
        with self.app.app_context():
            portfolio_id = self._portfolio()
        html = self.client.get(f"/portafolios/{portfolio_id}").data.decode()
        self.assertIn("traffic-light--red", html)
        self.assertIn("Drawdown:", html)
        self.assertIn("Semáforo de reglas", html)
        listing = self.client.get("/portafolios/").data.decode()
        self.assertIn("traffic-light--red", listing)
        page = self.client.get(f"/portafolios/{portfolio_id}/alertas").data.decode()
        self.assertIn("Alertas activas", page)
        self.assertIn("Drawdown", page)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_crashing_prices)
    def test_thresholds_are_editable(self, _download):
        self.sign_in()
        with self.app.app_context():
            portfolio_id = self._portfolio()
        form = {"alerts_form": "1"}
        for rule in alerts.RULES:
            form[f"alert_{rule.kind}_enabled"] = "1"
        form.update(alert_drawdown_yellow="40", alert_drawdown_red="50")
        response = self.post(f"/portafolios/{portfolio_id}/alertas", **form)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            portfolio = repositories.get_portfolio(self.user_id_of(), portfolio_id)
            rules = alerting.current_rules(portfolio)
            self.assertEqual(rules["drawdown"], (0.40, 0.50, True))
            self.assertEqual(rules["weight_drift"], (None, None, True))  # left empty
            self.assertFalse(any(e.kind == "drawdown" for e in repositories.open_alerts(portfolio)))

    def test_red_below_yellow_is_rejected(self):
        self.sign_in()
        with self.app.app_context():
            portfolio_id = self._portfolio()
        html = self.post(
            f"/portafolios/{portfolio_id}/alertas", alerts_form="1",
            alert_drawdown_enabled="1", alert_drawdown_yellow="20", alert_drawdown_red="10",
        ).data.decode()
        self.assertIn("el umbral rojo debe ser mayor o igual que el amarillo", html)

    def test_alerts_of_another_user_are_404(self):
        self.sign_in()
        with self.app.app_context():
            other = repositories.create_portfolio(
                "someone-else", name="Ajeno", model="hrp", benchmark="SPY", initial_capital=1,
                tracking_start=date.today(), holdings=[HoldingData("A", 1.0, 1.0, 1.0)],
            ).id
        self.assertEqual(self.client.get(f"/portafolios/{other}/alertas").status_code, 404)

    def test_fixing_stores_default_rules(self):
        with self.app.app_context():
            portfolio = repositories.create_portfolio(
                "x", name="P", model="hrp", benchmark="SPY", initial_capital=1,
                tracking_start=date.today(), holdings=[HoldingData("A", 1.0, 1.0, 1.0)],
            )
            alerting.ensure_rules(portfolio)
            self.assertEqual(
                {kind: values[:2] for kind, values in alerting.current_rules(portfolio).items()}, DEFAULTS
            )


if __name__ == "__main__":
    unittest.main()
