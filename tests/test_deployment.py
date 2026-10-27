"""Settings for hosted deployments (Render): database URL and reverse proxy."""

import os
import unittest
from unittest.mock import patch

from flask import request

from optimizacion_portafolios.web import create_app, database_url


class DatabaseUrlTests(unittest.TestCase):
    def test_postgres_urls_use_the_psycopg_driver(self):
        for url in ("postgres://u:p@host/db", "postgresql://u:p@host/db"):
            with self.subTest(url=url):
                self.assertEqual(database_url(url), "postgresql+psycopg://u:p@host/db")

    def test_other_urls_are_kept(self):
        self.assertEqual(database_url("sqlite:///instance/atlas.db"), "sqlite:///instance/atlas.db")
        self.assertEqual(database_url("postgresql+psycopg://h/db"), "postgresql+psycopg://h/db")
        self.assertIsNone(database_url(""))
        self.assertIsNone(database_url(None))


class ProxyTests(unittest.TestCase):
    def _client_addr(self, hops):
        with patch.dict(os.environ, {"ATLAS_PROXY_HOPS": hops}):
            app = create_app({"TESTING": True, "SECRET_KEY": "x"})

        # Answer before the sign-in guard: only the request's view of the client matters.
        app.before_request_funcs[None].insert(0, lambda: f"{request.remote_addr} {request.scheme}")
        response = app.test_client().get(
            "/",
            headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"},
            environ_base={"REMOTE_ADDR": "10.0.0.1"},
        )
        return response.get_data(as_text=True)

    def test_trusted_proxy_hop_gives_the_client_ip_and_scheme(self):
        self.assertEqual(self._client_addr("1"), "203.0.113.7 https")

    def test_forwarded_headers_are_ignored_without_trusted_proxies(self):
        self.assertEqual(self._client_addr(""), "10.0.0.1 http")


if __name__ == "__main__":
    unittest.main()
