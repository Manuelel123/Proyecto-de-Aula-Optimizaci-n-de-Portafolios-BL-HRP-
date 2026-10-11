"""HTTP tests for the full price history endpoint and its explorer switch."""

import json
import unittest
from unittest.mock import patch

import pandas as pd
from yfinance.exceptions import YFRateLimitError

from support import WebTestCase, make_prices

from optimizacion_portafolios.web.common import charts
from optimizacion_portafolios.web.history.services import (
    FULL_HISTORY_START,
    normalize_ticker,
)

_SERVICES = "optimizacion_portafolios.web.history.services"


def _long_prices(ticker: str, years: int = 20) -> pd.DataFrame:
    prices = make_prices((ticker,), rows=252 * years)
    prices.iloc[:3, 0] = float("nan")
    return prices


class AssetHistoryEndpointTests(WebTestCase):
    @patch(f"{_SERVICES}.download_prices")
    def test_returns_every_price_and_full_matrix(self, download) -> None:
        download.return_value = _long_prices("XLV")
        response = self.client.get("/api/historial?ticker=xlv")
        self.assertEqual(response.status_code, 200)
        tickers, start, _end = download.call_args.args
        self.assertEqual(tickers, ("XLV",))
        self.assertEqual(start, FULL_HISTORY_START)
        data = response.get_json()
        self.assertEqual(data["ticker"], "XLV")
        self.assertEqual(data["name"], "Sector salud (XLV)")
        self.assertEqual(data["start"], "2024-01-05")
        self.assertEqual(data["observations"], 252 * 20 - 3)
        self.assertEqual(data["years"], 20)
        prices = data["prices"]
        self.assertEqual(prices["ticker"], "XLV")
        self.assertEqual(len(prices["time"]), data["observations"])
        self.assertEqual(len(prices["time"]), len(prices["value"]))
        matrix = data["matrix"]
        self.assertEqual([trace["type"] for trace in matrix["data"]], ["heatmap", "heatmap"])
        self.assertNotIn("updatemenus", matrix["layout"])
        self.assertEqual(len(matrix["data"][0]["y"]), data["years"])
        self.assertIn("max-age", response.headers["Cache-Control"])

    def test_rejects_invalid_tickers_without_downloading(self) -> None:
        with patch(f"{_SERVICES}.download_prices") as download:
            for raw in ("", "bad ticker", "<script>", "A" * 30):
                with self.subTest(raw=raw):
                    response = self.client.get("/api/historial", query_string={"ticker": raw})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
            download.assert_not_called()

    @patch(f"{_SERVICES}.download_prices", return_value=pd.DataFrame())
    def test_unknown_ticker_is_not_found(self, _download) -> None:
        response = self.client.get("/api/historial?ticker=ZZZZQQ")
        self.assertEqual(response.status_code, 404)
        self.assertIn("ZZZZQQ", response.get_json()["error"])

    @patch(f"{_SERVICES}.download_prices", side_effect=YFRateLimitError())
    def test_rate_limit_is_reported(self, _download) -> None:
        response = self.client.get("/api/historial?ticker=AAPL")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Yahoo Finance limitó", response.get_json()["error"])

    def test_normalize_ticker_accepts_index_crypto_and_local_symbols(self) -> None:
        for raw, expected in (
            ("^gspc", "^GSPC"),
            ("btc-usd", "BTC-USD"),
            (" ecopetrol.cl ", "ECOPETROL.CL"),
            ("eurusd=x", "EURUSD=X"),
        ):
            self.assertEqual(normalize_ticker(raw), expected)


class HistorySwitchMarkupTests(WebTestCase):
    @patch("optimizacion_portafolios.web.monitoring.services.download_prices")
    def test_price_explorers_offer_full_history(self, download) -> None:
        download.return_value = make_prices(("AAPL", "MSFT"))
        response = self.post(
            "/monitoring",
            action="analyze",
            view="portfolio",
            universe="Portafolio actual",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
        )
        html = response.data.decode()
        self.assertIn('data-history-url="/api/historial"', html)
        self.assertIn('data-history-mode="full"', html)
        self.assertIn("Historial completo", html)
        self.assertIn("data-history-status", html)
        self.assertIn('data-range="5Y"', html)


class CompactMatrixRowsTests(unittest.TestCase):
    def test_long_histories_use_shorter_rows(self) -> None:
        index = pd.bdate_range("1990-01-01", "2025-12-31")
        returns = pd.Series(0.0005, index=index)
        figure = charts.returns_matrix_chart({"SPY": returns})
        layout = json.loads(str(figure))["layout"]
        years = 2025 - 1990 + 1
        self.assertEqual(layout["height"], 24 * years + 80)


if __name__ == "__main__":
    unittest.main()
