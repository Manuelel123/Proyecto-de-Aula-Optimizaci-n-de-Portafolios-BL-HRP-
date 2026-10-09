"""Tests for the Yahoo Finance data-access layer (network is always mocked)."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from yfinance.exceptions import YFRateLimitError

from optimizacion_portafolios.data.market_data import (
    _cached_ticker_info,
    download_prices,
    fetch_fundamental_information,
    fetch_market_cap_usd,
)


class MarketDataTests(unittest.TestCase):
    def tearDown(self) -> None:
        _cached_ticker_info.cache_clear()

    @patch("optimizacion_portafolios.data.market_data.yf.download")
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
        self.assertFalse(download.call_args.kwargs["threads"])

    @patch("optimizacion_portafolios.data.market_data.yf.Ticker")
    def test_fundamental_and_market_cap_share_cached_ticker_info(self, ticker):
        ticker.return_value.get_info.return_value = {
            "longName": "Example Corp.",
            "currency": "USD",
            "marketCap": 1_000_000_000,
        }

        profile = fetch_fundamental_information("CACHE-TEST")
        profile["longName"] = "Modified locally"
        market_cap = fetch_market_cap_usd("CACHE-TEST")

        self.assertEqual(market_cap, 1_000_000_000)
        self.assertEqual(ticker.return_value.get_info.call_count, 1)
        self.assertEqual(
            fetch_fundamental_information("CACHE-TEST")["longName"],
            "Example Corp.",
        )

    @patch("optimizacion_portafolios.data.market_data.sleep")
    @patch("optimizacion_portafolios.data.market_data.yf.Ticker")
    def test_ticker_info_retries_yahoo_rate_limit(self, ticker, sleep_mock):
        ticker.return_value.get_info.side_effect = [
            YFRateLimitError(),
            {"longName": "Recovered Corp."},
        ]

        self.assertEqual(
            fetch_fundamental_information("RETRY-TEST"),
            {"longName": "Recovered Corp."},
        )
        self.assertEqual(ticker.return_value.get_info.call_count, 2)
        sleep_mock.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
