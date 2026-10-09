"""Shared fixtures for the test suite (synthetic prices, Flask client with CSRF)."""

import re
import unittest

import numpy as np
import pandas as pd

from optimizacion_portafolios.web import create_app


def make_prices(tickers: tuple[str, ...], rows: int = 280) -> pd.DataFrame:
    """Deterministic geometric random-walk prices on business days."""
    seed = sum(ord(character) for ticker in tickers for character in ticker)
    rng = np.random.default_rng(seed)
    daily_returns = rng.normal(0.00035, 0.012, size=(rows, len(tickers)))
    prices = 100 * np.cumprod(1 + daily_returns, axis=0)
    index = pd.bdate_range("2024-01-02", periods=rows, name="Fecha")
    return pd.DataFrame(prices, index=index, columns=tickers)


class WebTestCase(unittest.TestCase):
    """Test case with an app client and a valid CSRF token."""

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
