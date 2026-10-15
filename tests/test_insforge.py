"""InsForge REST client and the persistent price store (no network)."""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pandas as pd
import requests

from optimizacion_portafolios.data import market_data, price_store
from optimizacion_portafolios.insforge import (
    InsForgeClient,
    InsForgeError,
    InsForgeSettings,
    PAGE_SIZE,
    in_filter,
)
from fake_insforge import FakeInsForge


def _response(status=200, body=None):
    response = mock.Mock()
    response.status_code = status
    response.reason = "Error"
    response.content = b"" if body is None else b"x"
    response.json.return_value = body
    return response


class InsForgeClientTests(unittest.TestCase):
    def setUp(self):
        self.http = mock.Mock(spec=requests.Session)
        self.client = InsForgeClient(
            InsForgeSettings("https://app.insforge.app", "anon-key", "api-key"),
            session=self.http,
        )

    def test_sign_in_uses_server_client_type_and_anon_key(self):
        self.http.request.return_value = _response(body={"accessToken": "a", "refreshToken": "r"})
        self.client.sign_in("ana@example.com", "secret")
        method, url = self.http.request.call_args.args
        kwargs = self.http.request.call_args.kwargs
        self.assertEqual((method, url), ("POST", "https://app.insforge.app/api/auth/sessions"))
        self.assertEqual(kwargs["params"], {"client_type": "server"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer anon-key")
        self.assertEqual(kwargs["json"]["method"], "password")

    def test_error_codes_become_spanish_messages(self):
        self.http.request.return_value = _response(
            401, {"error": "INVALID_CREDENTIALS", "message": "Invalid email or password"}
        )
        with self.assertRaises(InsForgeError) as caught:
            self.client.sign_in("ana@example.com", "bad")
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(caught.exception.user_message, "Correo o contraseña incorrectos.")

    def test_network_error_has_no_status(self):
        self.http.request.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(InsForgeError) as caught:
            self.client.public_config()
        self.assertIsNone(caught.exception.status)

    def test_select_pages_past_the_postgrest_cap(self):
        first = [{"n": index} for index in range(PAGE_SIZE)]
        self.http.request.side_effect = [_response(body=first), _response(body=[{"n": -1}])]
        rows = self.client.select("price_history", {"ticker": "eq.AAPL"}, admin=True)
        self.assertEqual(len(rows), PAGE_SIZE + 1)
        offsets = [call.kwargs["params"]["offset"] for call in self.http.request.call_args_list]
        self.assertEqual(offsets, [0, PAGE_SIZE])
        self.assertEqual(
            self.http.request.call_args.kwargs["headers"]["Authorization"], "Bearer api-key"
        )

    def test_user_tables_require_a_token(self):
        with self.assertRaises(InsForgeError):
            self.client.select("saved_portfolios")

    def test_upsert_sets_prefer_header_and_conflict_columns(self):
        self.http.request.return_value = _response(204)
        self.client.insert(
            "price_history", [{"ticker": "A"}], admin=True, upsert=True,
            on_conflict="ticker,day", returning=False,
        )
        kwargs = self.http.request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Prefer"], "return=minimal,resolution=merge-duplicates")
        self.assertEqual(kwargs["params"], {"on_conflict": "ticker,day"})

    def test_in_filter_quotes_values(self):
        self.assertEqual(in_filter(["GC=F", "^GSPC"]), 'in.("GC=F","^GSPC")')


class PriceStoreTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeInsForge()
        price_store.configure(self.fake, ttl_hours=24)
        market_data.clear_price_cache()
        self.addCleanup(price_store.configure, None)
        self.addCleanup(market_data.clear_price_cache)
        self.start, self.end = date(2025, 1, 1), date(2025, 1, 10)

    def _prices(self, tickers=("AAA", "BBB")):
        index = pd.bdate_range("2025-01-01", "2025-01-10", name="Fecha")
        return pd.DataFrame({ticker: range(1, len(index) + 1) for ticker in tickers}, index=index, dtype=float)

    def test_disabled_without_admin_key(self):
        price_store.configure(FakeInsForge(admin_key=None))
        self.assertFalse(price_store.enabled())

    def test_download_is_written_and_then_served_from_the_store(self):
        with mock.patch.object(market_data, "_download_prices_uncached", return_value=self._prices()) as download:
            first = market_data.download_prices(("AAA", "BBB"), self.start, self.end)
        download.assert_called_once()
        self.assertEqual(len(self.fake.tables["price_history"]), 2 * len(first))
        market_data.clear_price_cache()
        with mock.patch.object(market_data, "_download_prices_uncached") as download:
            second = market_data.download_prices(("AAA", "BBB"), self.start, self.end)
        download.assert_not_called()
        pd.testing.assert_frame_equal(first, second, check_freq=False)

    def test_only_missing_tickers_are_downloaded(self):
        price_store.save(self._prices(("AAA",)), self.start, self.end)
        with mock.patch.object(
            market_data, "_download_prices_uncached", return_value=self._prices(("BBB",))
        ) as download:
            prices = market_data.download_prices(("AAA", "BBB"), self.start, self.end)
        self.assertEqual(download.call_args.args[0], ("BBB",))
        self.assertEqual(list(prices.columns), ["AAA", "BBB"])

    def test_stale_or_short_coverage_is_downloaded_again(self):
        price_store.save(self._prices(("AAA",)), self.start, self.end)
        stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        self.fake.tables["price_coverage"][0]["fetched_at"] = stale
        self.assertEqual(price_store.load(("AAA",), self.start, self.end)[1], ("AAA",))
        self.fake.tables["price_coverage"][0]["fetched_at"] = datetime.now(timezone.utc).isoformat()
        self.assertEqual(price_store.load(("AAA",), self.start, date(2025, 2, 1))[1], ("AAA",))
        self.assertEqual(price_store.load(("AAA",), self.start, self.end)[1], ())

    def test_overlapping_fresh_coverage_is_merged(self):
        price_store.save(self._prices(("AAA",)), self.start, self.end)
        price_store.save(self._prices(("AAA",)), date(2025, 1, 5), date(2025, 1, 20))
        coverage = self.fake.tables["price_coverage"][0]
        self.assertEqual((coverage["start_date"], coverage["end_date"]), ("2025-01-01", "2025-01-20"))

    def test_store_failures_fall_back_to_downloads(self):
        with mock.patch.object(self.fake, "select", side_effect=InsForgeError("down", 503)):
            stored, pending = price_store.load(("AAA",), self.start, self.end)
        self.assertEqual((stored, pending), ({}, ("AAA",)))


if __name__ == "__main__":
    unittest.main()
