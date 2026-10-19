"""F2: buy-and-hold valuation, USD conversion and fixing a portfolio."""

import re
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from optimizacion_portafolios.analytics import tracking
from optimizacion_portafolios.analytics.tracking import Segment
from optimizacion_portafolios.data import fx
from optimizacion_portafolios.storage import repositories
from support import make_prices
from test_web_accounts import AccountTestCase

DAYS = pd.bdate_range("2026-01-05", periods=4, name="Fecha")


class ValuationTests(unittest.TestCase):
    def setUp(self):
        self.prices = pd.DataFrame(
            {"A": [10.0, 11.0, 12.0, 12.0], "B": [20.0, 20.0, 18.0, 22.0]}, index=DAYS
        )

    def test_initial_positions_pay_the_commission_first(self):
        positions = tracking.initial_positions(
            100_000, {"A": 0.6, "B": 0.4}, {"A": 10.0, "B": 20.0}, commission_bps=10
        )
        self.assertAlmostEqual(positions.cost, 100.0)
        self.assertAlmostEqual(positions.invested, 99_900.0)
        self.assertAlmostEqual(positions.units["A"], 99_900 * 0.6 / 10)
        self.assertAlmostEqual(positions.units["B"], 99_900 * 0.4 / 20)

    def test_initial_positions_require_every_price(self):
        with self.assertRaises(ValueError):
            tracking.initial_positions(1_000, {"A": 0.5, "B": 0.5}, {"A": 10.0})

    def test_buy_and_hold_value_and_drifting_weights(self):
        values = tracking.holding_values(self.prices, [Segment(DAYS[0], {"A": 60.0, "B": 20.0})])
        total = values.sum(axis=1)
        self.assertEqual(list(total), [1000.0, 1060.0, 1080.0, 1160.0])
        weights = tracking.current_weights(values)
        self.assertAlmostEqual(weights["A"], 720 / 1160)
        drift = tracking.weight_drift(weights, {"A": 0.6, "B": 0.4})
        self.assertAlmostEqual(drift["A"], 720 / 1160 - 0.6)
        self.assertAlmostEqual(drift.sum(), 0.0)

    def test_segments_switch_units_on_their_start_date(self):
        segments = [
            Segment(DAYS[0], {"A": 60.0, "B": 20.0}),
            Segment(DAYS[2], {"A": 45.0, "B": 30.0}),  # rebalanced on day 3
        ]
        total = tracking.portfolio_values(self.prices, segments)
        self.assertEqual(list(total), [1000.0, 1060.0, 45 * 12 + 30 * 18, 45 * 12 + 30 * 22])

    def test_delisted_ticker_keeps_its_last_price(self):
        prices = self.prices.copy()
        prices.loc[DAYS[2]:, "B"] = float("nan")
        total = tracking.portfolio_values(prices, [Segment(DAYS[0], {"A": 60.0, "B": 20.0})])
        self.assertEqual(total.iloc[-1], 60 * 12 + 20 * 20)

    def test_missing_price_column_is_an_error(self):
        with self.assertRaises(ValueError):
            tracking.holding_values(self.prices, [Segment(DAYS[0], {"C": 1.0})])

    def test_summary_against_rebased_benchmark(self):
        total = pd.Series([1000.0, 1100.0], index=DAYS[:2])
        benchmark = pd.Series([50.0, 52.5], index=DAYS[:2])
        summary = tracking.tracking_summary(total, benchmark, 1000.0)
        self.assertAlmostEqual(summary["total_return"], 0.10)
        self.assertAlmostEqual(summary["benchmark_return"], 0.05)
        self.assertAlmostEqual(summary["excess_return"], 0.05)


class CurrencyTests(unittest.TestCase):
    def setUp(self):
        fx.ticker_currency.cache_clear()
        self.addCleanup(fx.ticker_currency.cache_clear)

    def test_tiingo_and_crypto_tickers_are_usd_without_lookups(self):
        with patch.object(fx, "fetch_fundamental_information") as info:
            self.assertEqual(fx.ticker_currency("AAPL"), ("USD", 1.0))
            self.assertEqual(fx.ticker_currency("BTC-USD"), ("USD", 1.0))
        info.assert_not_called()

    def test_foreign_prices_are_converted_with_daily_rates(self):
        prices = pd.DataFrame({"MINEROS.CL": [4000.0, 4100.0], "AAPL": [200.0, 201.0]}, index=DAYS[:2])
        rates = pd.DataFrame({"USDCOP=X": [4000.0, 4100.0]}, index=DAYS[:2])

        def download(tickers, _start, _end):
            return rates if tickers == ("USDCOP=X",) else pd.DataFrame()

        with patch.object(fx, "fetch_fundamental_information", return_value={"currency": "COP"}), \
                patch.object(fx, "download_prices", side_effect=download):
            converted = fx.prices_in_usd(prices, DAYS[0].date(), DAYS[1].date())
        self.assertEqual(list(converted["MINEROS.CL"]), [1.0, 1.0])
        self.assertEqual(list(converted["AAPL"]), [200.0, 201.0])

    def test_pence_are_divided_by_100(self):
        with patch.object(fx, "fetch_fundamental_information", return_value={"currency": "GBp"}):
            self.assertEqual(fx.ticker_currency("VOD.L"), ("GBP", 0.01))


def _prices(tickers, start, end):
    """Synthetic closes for any request, ending on ``end``."""
    frame = make_prices(tuple(tickers), rows=300)
    frame.index = pd.bdate_range(end=pd.Timestamp(end), periods=len(frame), name="Fecha")
    return frame.loc[frame.index >= pd.Timestamp(start)]


class FixPortfolioFlowTests(AccountTestCase):
    def _hrp_page(self):
        return self.post(
            "/hrp", action="optimize", universe="Portafolio actual", benchmark="S&P 500 (SPY)",
            start_date="2024-01-01", tickers=["AAPL", "MSFT"], custom_tickers="", backtest="",
        ).data.decode()

    def _snapshot(self, html):
        return re.search(r'name="snapshot" value="([^"]+)"', html).group(1)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=_prices)
    def test_anonymous_result_invites_to_sign_in(self, _download):
        html = self._hrp_page()
        self.assertIn("Inicia sesión para fijar este portafolio", html)
        self.assertNotIn('name="snapshot"', html)

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices", side_effect=_prices)
    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=_prices)
    def test_optimize_fix_and_track(self, _hrp_download, _portfolio_download):
        self.sign_in()
        snapshot = self._snapshot(self._hrp_page())
        form = self.post("/portafolios/fijar", snapshot=snapshot, step="form").data.decode()
        self.assertIn("Establecer como portafolio óptimo", form)
        self.assertIn('value="100000"', form)

        response = self.post(
            "/portafolios/fijar", snapshot=snapshot, step="confirm", name="Mi HRP",
            capital="100000", commission_bps="10", benchmark="SPY",
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            user_id = self.insforge.users["ana@example.com"]["id"]
            portfolio = repositories.list_portfolios(user_id)[0]
            version = portfolio.current_version
            self.assertEqual((portfolio.name, portfolio.model, portfolio.benchmark), ("Mi HRP", "hrp", "SPY"))
            last_business_day = pd.bdate_range(end=pd.Timestamp(date.today()), periods=1)[0].date()
            self.assertEqual(portfolio.tracking_start, last_business_day)
            self.assertAlmostEqual(sum(h.target_weight for h in version.holdings), 1.0)
            invested = sum(h.units * h.entry_price for h in version.holdings)
            self.assertAlmostEqual(invested, 100_000 * (1 - 10 / 10_000))
            self.assertAlmostEqual(version.cost, 100.0)
            self.assertEqual(version.params["universe"], "Portafolio actual")
            self.assertIn("bounds", version.params)
            self.assertEqual(len(version.transactions), 2)
            portfolio_id = portfolio.id

        detail = self.client.get(f"/portafolios/{portfolio_id}").data.decode()
        self.assertIn("Valor actual", detail)
        self.assertIn('id="chart-tracking_value"', detail)
        self.assertIn('id="chart-tracking_weights"', detail)
        self.assertIn("Capital nocional", detail)
        for title in ("Rentabilidad", "Riesgo", "Ajustadas por riesgo", "Frente al benchmark", "Estructura"):
            self.assertIn(title, detail)
        with self.app.app_context():
            snapshots = repositories.latest_snapshots(repositories.get_portfolio(user_id, portfolio_id), 5)
            self.assertEqual(len(snapshots), 1)  # fixed today: one valued day
            self.assertIsNotNone(snapshots[0].metrics)
        listing = self.client.get("/portafolios/").data.decode()
        self.assertIn("Mi HRP", listing)

    def test_tampered_snapshot_is_rejected(self):
        self.sign_in()
        response = self.post("/portafolios/fijar", snapshot="forged", step="confirm", name="X")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(repositories.active_portfolios(), [])

    @patch("optimizacion_portafolios.web.portfolios.services.download_prices")
    @patch("optimizacion_portafolios.web.hrp.services.download_prices", side_effect=_prices)
    def test_missing_prices_block_the_fixing(self, _hrp_download, portfolio_download):
        self.sign_in()
        snapshot = self._snapshot(self._hrp_page())
        portfolio_download.side_effect = lambda tickers, s, e: _prices([t for t in tickers if t != "MSFT"], s, e)
        html = self.post(
            "/portafolios/fijar", snapshot=snapshot, step="confirm", name="X",
            capital="100000", commission_bps="10", benchmark="SPY",
        ).data.decode()
        self.assertIn("No hay precios recientes para: MSFT", html)
        with self.app.app_context():
            self.assertEqual(repositories.active_portfolios(), [])

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices", side_effect=_prices)
    def test_black_litterman_snapshot_carries_its_parameters(self, _download, market_cap):
        self.sign_in()
        market_cap.side_effect = {"AAPL": 3e12, "MSFT": 3.5e12}.__getitem__
        html = self.post(
            "/black-litterman", action="optimize", universe="Activos principales",
            benchmark="S&P 500 (SPY)", objective="Máximo ratio de Sharpe", risk_free_rate="2.0",
            tickers=["AAPL", "MSFT"], custom_tickers="", view_AAPL="8.0", view_MSFT="9.0",
        ).data.decode()
        self.assertIn("Establecer como portafolio óptimo", html)
        with self.app.test_request_context():
            from optimizacion_portafolios.web.portfolios.snapshots import unsign

            snapshot = unsign(self._snapshot(html))
        self.assertEqual(snapshot["model"], "black_litterman")
        self.assertEqual(snapshot["params"]["objective"], "Máximo ratio de Sharpe")
        self.assertAlmostEqual(snapshot["params"]["risk_free_rate"], 0.02)
        self.assertAlmostEqual(snapshot["params"]["views"]["AAPL"], 0.08)


if __name__ == "__main__":
    unittest.main()
