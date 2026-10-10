"""HTTP tests for the monitoring page."""

import unittest
from unittest.mock import patch

from yfinance.exceptions import YFRateLimitError

from support import WebTestCase, make_prices


class MonitoringPageTests(WebTestCase):
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


if __name__ == "__main__":
    unittest.main()
