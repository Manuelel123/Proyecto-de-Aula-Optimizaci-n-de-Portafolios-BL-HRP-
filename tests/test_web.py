"""HTTP-level tests for the Flask pages and their portfolio workflows."""

import unittest
from unittest.mock import patch

from yfinance.exceptions import YFRateLimitError

from support import WebTestCase, make_prices


class FlaskInterfaceTests(WebTestCase):
    def test_all_primary_pages_render(self) -> None:
        for path, title in (
            ("/", "ATLAS"),
            ("/monitoring", "Monitoreo de activos"),
            ("/hrp", "Optimización HRP"),
            ("/black-litterman", "Black-Litterman"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(title.encode(), response.data)

    def test_post_requires_csrf_token(self) -> None:
        response = self.client.post("/hrp", data={"action": "optimize"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("sesión del formulario expiró".encode(), response.data)

    def test_invalid_custom_ticker_is_reported_without_market_request(self) -> None:
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["<script>"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("no es válido".encode(), response.data)

    def test_invalid_custom_benchmark_is_reported_without_market_request(self) -> None:
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            custom_benchmark="<script>",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("no es válido".encode(), response.data)

    @patch("optimizacion_portafolios.web.monitoring.services.download_prices")
    def test_monitoring_post_renders_prices_and_risk_tables(self, download) -> None:
        download.return_value = make_prices(("AAPL",))
        response = self.post(
            "/monitoring",
            action="analyze",
            view="portfolio",
            portfolio="Portafolio actual",
            start_date="2024-01-01",
            tickers=["AAPL"],
            volatility_ticker="AAPL",
            custom_tickers="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Evolución de precios ajustados".encode(), response.data)
        self.assertIn("Volatilidad mensual actual".encode(), response.data)
        self.assertIn(b'<option value="AAPL" selected>', response.data)
        download.assert_called_once()

    @patch("optimizacion_portafolios.web.monitoring.services.download_prices")
    def test_options_view_renders_period_volatility(self, download) -> None:
        download.return_value = make_prices(("UEC", "EQT"))
        response = self.post(
            "/monitoring",
            action="analyze",
            view="options",
            start_date="2024-01-01",
            period="Mensual",
            tickers=["UEC", "EQT"],
            custom_tickers="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VH mensual", response.data)
        self.assertIn(b'<option value="Mensual" selected>', response.data)

    @patch("optimizacion_portafolios.web.monitoring.services.fetch_fundamental_information")
    def test_fundamental_analysis_renders_company_information(self, fetch_info) -> None:
        fetch_info.return_value = {
            "longName": "Apple Inc.",
            "currency": "USD",
            "currentPrice": 200.0,
            "marketCap": 3_000_000_000_000,
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "country": "United States",
            "fiftyTwoWeekLow": 150.0,
            "fiftyTwoWeekHigh": 250.0,
            "revenueGrowth": 0.1,
        }
        response = self.post(
            "/monitoring",
            action="analyze",
            view="fundamental",
            portfolio="Portafolio actual",
            ticker="AAPL",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Apple Inc.", response.data)
        self.assertIn(b"Technology", response.data)
        self.assertIn("Capitalización bursátil".encode(), response.data)
        fetch_info.assert_called_once_with("AAPL")

    @patch(
        "optimizacion_portafolios.web.monitoring.services.fetch_fundamental_information",
        side_effect=YFRateLimitError(),
    )
    def test_fundamental_analysis_reports_yahoo_rate_limit(self, _fetch_info) -> None:
        response = self.post(
            "/monitoring",
            action="analyze",
            view="fundamental",
            portfolio="Portafolio actual",
            ticker="AAPL",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Yahoo Finance limitó temporalmente".encode(), response.data)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_optimization_renders_result_and_quantstats(self, download) -> None:
        def price_response(tickers, _start_date, _end_date):
            return make_prices(tickers)

        download.side_effect = price_response
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            custom_benchmark="^GSPC",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Distribución del portafolio".encode(), response.data)
        self.assertIn("Análisis histórico".encode(), response.data)
        self.assertIn("Descargar informe QuantStats".encode(), response.data)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args_list[1].args[0], ("^GSPC",))

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_report_download_returns_html_attachment(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="download_report",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn(
            "reporte_quantstats_hrp.html",
            response.headers["Content-Disposition"],
        )

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
