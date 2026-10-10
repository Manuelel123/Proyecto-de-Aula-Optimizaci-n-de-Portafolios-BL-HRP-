"""HTTP-level tests for the Flask pages and their portfolio workflows."""

import re
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

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_applies_weight_limits_and_keeps_them_in_forms(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT", "XLV"],
            custom_tickers="",
            min_weight="10",
            max_weight="50",
            min_AAPL="",
            max_AAPL="",
            min_MSFT="40",
            max_MSFT="",
            min_XLV="",
            max_XLV="20",
        )
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn("Ratio de Sortino anualizado", html)
        self.assertIn("Ratio de Sortino (QuantStats", html)
        self.assertIn('name="min_weight" value="10"', html)
        self.assertIn('name="max_weight" value="50"', html)
        self.assertIn('name="min_MSFT" value="40"', html)  # hidden report field
        self.assertIn('name="max_XLV" value="20"', html)
        self.assertIn('id="min_MSFT" name="min_MSFT" type="number"', html)

        weights = self._hrp_weights(html)
        self.assertAlmostEqual(sum(weights.values()), 100.0, places=1)
        self.assertGreaterEqual(weights["MSFT"], 40.0 - 0.01)
        self.assertLessEqual(weights["XLV"], 20.0 + 0.01)
        for value in weights.values():
            self.assertTrue(10.0 - 0.01 <= value <= 50.0 + 0.01)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_report_download_accepts_weight_limits(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="download_report",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
            min_weight="20",
            max_weight="80",
            max_AAPL="30",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_reports_infeasible_or_invalid_weight_limits(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        cases = {
            "suma de los pesos mínimos": {"min_weight": "60"},
            "suma de los pesos máximos": {"max_weight": "40"},
            "no puede superar al máximo en: AAPL": {
                "min_AAPL": "30",
                "max_AAPL": "20",
            },
            "peso máximo debe estar entre 0 % y 100 %": {"max_weight": "150"},
            "peso mínimo de MSFT debe ser numérico": {"min_MSFT": "abc"},
        }
        for message, limits in cases.items():
            with self.subTest(message=message):
                response = self.post(
                    "/hrp",
                    action="optimize",
                    universe="Portafolio actual",
                    benchmark="S&P 500 (SPY)",
                    start_date="2024-01-01",
                    tickers=["AAPL", "MSFT"],
                    custom_tickers="",
                    **limits,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(message.encode(), response.data)
                self.assertNotIn("Distribución del portafolio".encode(), response.data)

    @staticmethod
    def _hrp_weights(html: str) -> dict[str, float]:
        table = html.split("Distribución del portafolio", 1)[1].split("</table>", 1)[0]
        rows = re.findall(
            r"<tr>\s*<td>([^<]+)</td>\s*<td>[^<]+</td>\s*<td>([\d.,]+)\s*%</td>",
            table,
        )
        return {ticker: float(value.replace(",", ".")) for ticker, value in rows}

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
            objective="Máximo Sharpe",
            risk_free_rate="2.0",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
            view_AAPL="8.0",
            view_MSFT="8.0",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Yahoo Finance limitó temporalmente".encode(), response.data)

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_runs_and_renders_posterior(
        self, download, market_cap
    ) -> None:
        download.side_effect = lambda tickers, _start_date, _end_date: make_prices(
            tickers
        )
        market_cap.side_effect = {
            "AAPL": 3_000_000_000_000,
            "MSFT": 3_500_000_000_000,
            "GOOGL": 2_000_000_000_000,
        }.__getitem__
        response = self.post(
            "/black-litterman",
            action="optimize",
            universe="Activos principales",
            benchmark="S&P 500 (SPY)",
            objective="Máximo Sharpe",
            risk_free_rate="2.0",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT", "GOOGL"],
            custom_tickers="",
            view_AAPL="8.0",
            view_MSFT="8.0",
            view_GOOGL="8.0",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Asignación del portafolio".encode(), response.data)
        self.assertIn("Covarianza posterior".encode(), response.data)
        self.assertIn("Análisis histórico".encode(), response.data)
        self.assertEqual(market_cap.call_count, 3)


if __name__ == "__main__":
    unittest.main()
