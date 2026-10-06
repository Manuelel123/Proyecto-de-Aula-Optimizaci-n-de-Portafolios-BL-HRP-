"""Focused tests for the Flask interface and its portfolio workflows."""

import re
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.market_data import download_prices


def make_prices(tickers: tuple[str, ...], rows: int = 280) -> pd.DataFrame:
    seed = sum(ord(character) for ticker in tickers for character in ticker)
    rng = np.random.default_rng(seed)
    daily_returns = rng.normal(0.00035, 0.012, size=(rows, len(tickers)))
    prices = 100 * np.cumprod(1 + daily_returns, axis=0)
    index = pd.bdate_range("2024-01-02", periods=rows, name="Fecha")
    return pd.DataFrame(prices, index=index, columns=tickers)


class FlaskInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        response = self.client.get("/hrp")
        self.assertEqual(response.status_code, 200)
        match = re.search(
            rb'name="csrf_token"\s+value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        self.csrf_token = match.group(1).decode()

    def post(self, path: str, **data):
        data["csrf_token"] = self.csrf_token
        return self.client.post(path, data=data)

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

    @patch("optimizacion_portafolios.web.monitoring.download_prices")
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

    @patch("optimizacion_portafolios.web.hrp.download_prices")
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

    @patch("optimizacion_portafolios.web.black_litterman.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.download_prices")
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


class MarketDataTests(unittest.TestCase):
    @patch("optimizacion_portafolios.market_data.yf.download")
    def test_download_prices_selects_close_level_from_multiindex(self, download):
        index = pd.bdate_range("2025-01-01", periods=3, name="Date")
        columns = pd.MultiIndex.from_product(
            [["Close", "Open"], ["AAPL", "MSFT"]]
        )
        values = np.arange(12, dtype=float).reshape(3, 4)
        download.return_value = pd.DataFrame(values, index=index, columns=columns)

        result = download_prices(("MSFT", "AAPL"), index[0].date(), index[-1].date())

        self.assertEqual(list(result.columns), ["MSFT", "AAPL"])
        self.assertEqual(result.index.name, "Fecha")
        self.assertEqual(result.iloc[0].tolist(), [1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
