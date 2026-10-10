"""HTTP tests for the Black-Litterman page."""

import unittest
from unittest.mock import patch

from yfinance.exceptions import YFRateLimitError

from support import WebTestCase, make_prices


class BlackLittermanPageTests(WebTestCase):
    @staticmethod
    def _mock_market_data(download, market_cap) -> None:
        download.side_effect = lambda tickers, _start_date, _end_date: make_prices(
            tickers
        )
        market_cap.side_effect = {
            "AAPL": 3_000_000_000_000,
            "MSFT": 3_500_000_000_000,
            "GOOGL": 2_000_000_000_000,
            "NVDA": 4_000_000_000_000,
        }.__getitem__

    @patch(
        "optimizacion_portafolios.web.black_litterman.routes.run_black_litterman",
        side_effect=YFRateLimitError(),
    )
    def test_black_litterman_reports_yahoo_rate_limit(self, _run_model) -> None:
        response = self.post(
            "/black-litterman",
            action="optimize",
            universe="Activos principales",
            benchmark="S&P 500 (SPY)",
            objective="Máximo ratio de Sharpe",
            risk_free_rate="2.0",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
            view_AAPL="8.0",
            view_MSFT="8.0",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Yahoo Finance limitó temporalmente".encode(), response.data)

    def _black_litterman_form(self, **overrides) -> dict:
        form = {
            "action": "optimize",
            "universe": "Activos principales",
            "benchmark": "S&P 500 (SPY)",
            "objective": "Máximo ratio de Sharpe",
            "risk_free_rate": "2.0",
            "start_date": "2024-01-01",
            "tickers": ["AAPL", "MSFT"],
            "custom_tickers": "nvda",
            "view_AAPL": "8.0",
            "view_MSFT": "9.0",
            "view_NVDA": "25.0",
        }
        form.update(overrides)
        return form

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_runs_and_renders_posterior(
        self, download, market_cap
    ) -> None:
        self._mock_market_data(download, market_cap)
        response = self.post(
            "/black-litterman",
            **self._black_litterman_form(
                tickers=["AAPL", "MSFT", "GOOGL"],
                custom_tickers="",
                view_GOOGL="8.0",
            ),
        )
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn("Asignación del portafolio", page)
        self.assertIn("Covarianza posterior", page)
        self.assertIn("Retorno prior (equilibrio)", page)
        self.assertIn("Ratio de Sortino esperado (modelo)", page)
        self.assertIn("Ratio de Sortino histórico", page)
        self.assertIn("Análisis histórico", page)
        self.assertEqual(market_cap.call_count, 3)

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_uses_custom_ticker_view(
        self, download, market_cap
    ) -> None:
        self._mock_market_data(download, market_cap)
        for objective in ("Máximo ratio de Sortino", "Mínimo CVaR (95%)"):
            with self.subTest(objective=objective):
                response = self.post(
                    "/black-litterman",
                    **self._black_litterman_form(objective=objective),
                )
                self.assertEqual(response.status_code, 200)
                page = response.data.decode()
                self.assertIn("Asignación del portafolio", page)
                self.assertIn("<td>25.00%</td>", page)
                self.assertIn('name="view_NVDA" value="25.0"', page)
                self.assertIn('id="view_NVDA"', page)
        self.assertIn("NVDA", {call.args[0] for call in market_cap.call_args_list})

    def test_black_litterman_refresh_shows_custom_ticker_views(self) -> None:
        response = self.post(
            "/black-litterman",
            **self._black_litterman_form(
                action="refresh", custom_tickers="nvda, bad ticker"
            ),
        )
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn('id="view_NVDA"', page)
        self.assertIn('id="view_AAPL"', page)
        self.assertIn('value="95%"', page)
        self.assertIn("Símbolos no válidos ignorados", page)

    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_requires_visible_view_for_custom_ticker(
        self, download
    ) -> None:
        form = self._black_litterman_form()
        del form["view_NVDA"]
        response = self.post("/black-litterman", **form)
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn("Define la view anual de: NVDA", page)
        self.assertIn('id="view_NVDA"', page)
        download.assert_not_called()

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_report_download_keeps_custom_views(
        self, download, market_cap
    ) -> None:
        self._mock_market_data(download, market_cap)
        response = self.post(
            "/black-litterman",
            **self._black_litterman_form(action="download_report"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "reporte_quantstats_black_litterman.html",
            response.headers["Content-Disposition"],
        )


if __name__ == "__main__":
    unittest.main()
