"""Accounts (InsForge Auth), saved portfolios and backtest history pages."""

import base64
import hashlib
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from optimizacion_portafolios.models.hrp_backtest import clear_backtest_cache
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.auth.session import safe_next
from optimizacion_portafolios.web.auth.throttle import login_throttle
from fake_insforge import FakeInsForge, make_jwt
from support import WebTestCase, make_prices

_CSRF = re.compile(rb'name="csrf_token"\s+value="([^"]+)"')


class AccountTestCase(unittest.TestCase):
    def setUp(self) -> None:
        login_throttle.clear()
        self.insforge = FakeInsForge()
        self.app = create_app(
            {"TESTING": True, "SECRET_KEY": "test-secret", "INSFORGE_CLIENT": self.insforge}
        )
        self.client = self.app.test_client()
        self.csrf_token = _CSRF.search(self.client.get("/auth/ingresar").data).group(1).decode()

    def post(self, path: str, **data):
        data["csrf_token"] = self.csrf_token
        return self.client.post(path, data=data)

    def sign_in(self, email="ana@example.com", password="secreto1"):
        if email not in self.insforge.users:
            self.insforge.add_user(email, password)
        response = self.post("/auth/ingresar", email=email, password=password)
        self.assertEqual(response.status_code, 302)
        return response


class AuthFlowTests(AccountTestCase):
    def test_login_page_lists_oauth_providers(self):
        html = self.client.get("/auth/ingresar").data.decode()
        self.assertIn("Continuar con Google", html)
        self.assertIn("Continuar con GitHub", html)

    def test_sign_in_stores_session_and_shows_user_menu(self):
        response = self.sign_in()
        self.assertEqual(urlsplit(response.location).path, "/")
        with self.client.session_transaction() as session:
            self.assertTrue(session["auth"]["access"])
            self.assertEqual(session["auth"]["user"]["email"], "ana@example.com")
        html = self.client.get("/").data.decode()
        self.assertIn("Mis portafolios", html)
        self.assertIn("Cerrar sesión", html)

    def test_wrong_password_is_reported_in_spanish(self):
        self.insforge.add_user("ana@example.com", "secreto1")
        response = self.post("/auth/ingresar", email="ana@example.com", password="mala")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Correo o contraseña incorrectos.", response.data.decode())

    def test_sign_up_requires_email_code_then_signs_in(self):
        response = self.post(
            "/auth/registro", name="Ana", email="Nueva@Example.com",
            password="secreto1", password_confirm="secreto1",
        )
        self.assertEqual(urlsplit(response.location).path, "/auth/verificar")
        self.assertIn("nueva@example.com", self.client.get("/auth/verificar").data.decode())
        wrong = self.post("/auth/verificar", code="000000")
        self.assertIn("El código no es válido o expiró.", wrong.data.decode())
        response = self.post("/auth/verificar", code="123 456")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertEqual(session["auth"]["user"]["email"], "nueva@example.com")

    def test_sign_up_validates_password_before_calling_insforge(self):
        response = self.post(
            "/auth/registro", email="ana@example.com", password="abc", password_confirm="abc"
        )
        self.assertIn("al menos 6 caracteres", response.data.decode())
        self.assertNotIn(("sign_up", "ana@example.com"), self.insforge.calls)

    def test_existing_email_cannot_sign_up_again(self):
        self.insforge.add_user("ana@example.com", "secreto1")
        response = self.post(
            "/auth/registro", email="ana@example.com", password="secreto1", password_confirm="secreto1"
        )
        self.assertIn("Ya existe una cuenta con ese correo.", response.data.decode())

    def test_unverified_sign_in_sends_a_new_code(self):
        self.insforge.add_user("ana@example.com", "secreto1", verified=False)
        response = self.post("/auth/ingresar", email="ana@example.com", password="secreto1")
        self.assertEqual(urlsplit(response.location).path, "/auth/verificar")
        self.assertIn(("send_verification", "ana@example.com"), self.insforge.calls)

    def test_password_reset_with_code(self):
        self.insforge.add_user("ana@example.com", "vieja12")
        self.post("/auth/recuperar", email="ana@example.com")
        response = self.post(
            "/auth/restablecer", code="654321", password="nueva123", password_confirm="nueva123"
        )
        self.assertEqual(urlsplit(response.location).path, "/auth/ingresar")
        self.assertEqual(self.insforge.users["ana@example.com"]["password"], "nueva123")

    def test_oauth_uses_pkce_and_exchanges_the_code(self):
        response = self.client.get("/auth/oauth/google?next=/portafolios/")
        self.assertTrue(response.location.startswith("https://accounts.example/google"))
        _, provider, redirect_uri, challenge = self.insforge.calls[-1]
        self.assertEqual(provider, "google")
        self.assertTrue(redirect_uri.endswith("/auth/oauth/callback"))
        response = self.client.get("/auth/oauth/callback?insforge_code=abc")
        self.assertEqual(urlsplit(response.location).path, "/portafolios/")
        _, code, verifier = self.insforge.calls[-1]
        self.assertEqual(code, "abc")
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(expected, challenge)

    def test_unknown_oauth_provider_is_rejected(self):
        response = self.client.get("/auth/oauth/myspace")
        self.assertEqual(urlsplit(response.location).path, "/auth/ingresar")

    def test_expired_access_token_is_refreshed(self):
        self.sign_in()
        with self.client.session_transaction() as session:
            state = session["auth"]
            state["access"] = make_jwt(lifetime=-10)
            session["auth"] = state
        response = self.client.get("/portafolios/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(("refresh",), self.insforge.calls)

    def test_rejected_refresh_signs_the_user_out(self):
        self.sign_in()
        with self.client.session_transaction() as session:
            session["auth"] = {"access": make_jwt(lifetime=-10), "refresh": "revoked", "user": {}}
        response = self.client.get("/portafolios/")
        self.assertEqual(urlsplit(response.location).path, "/auth/ingresar")
        with self.client.session_transaction() as session:
            self.assertNotIn("auth", session)

    def test_logout_clears_the_session(self):
        self.sign_in()
        self.post("/auth/salir")
        with self.client.session_transaction() as session:
            self.assertNotIn("auth", session)

    def test_account_pages_require_sign_in(self):
        response = self.client.get("/cuenta/backtests")
        location = urlsplit(response.location)
        self.assertEqual(location.path, "/auth/ingresar")
        self.assertEqual(parse_qs(location.query)["next"], ["/cuenta/backtests"])

    def test_safe_next_blocks_external_redirects(self):
        with self.app.test_request_context():
            self.assertEqual(safe_next("/portafolios/"), "/portafolios/")
            for target in ("https://evil.example", "//evil.example", "javascript:alert(1)"):
                self.assertEqual(safe_next(target), "/")


class BacktestHistoryTests(AccountTestCase):
    def _post_backtest(self):
        return self.post(
            "/hrp", action="optimize", universe="Portafolio actual", benchmark="S&P 500 (SPY)",
            start_date="2024-01-01", tickers=["AAPL", "MSFT", "XLV"], custom_tickers="",
            backtest="1", bt_lookback="60",
        ).data.decode()

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_backtest_is_recorded_once_per_inputs(self, download):
        clear_backtest_cache()
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        self.sign_in()
        html = self._post_backtest()
        self.assertIn("Backtest guardado en tu historial.", html)
        runs = self.insforge.tables["backtest_runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["tickers"], ["AAPL", "MSFT", "XLV"])
        results = self.insforge.tables["backtest_results"]
        self.assertEqual(len(results), 7)
        monthly = next(row for row in results if row["strategy"] == "Mensual")
        self.assertEqual(monthly["rebalances"], len(monthly["rebalance_dates"]))

        again = self._post_backtest()
        self.assertIn("Este backtest ya estaba en tu historial.", again)
        self.assertEqual(len(self.insforge.tables["backtest_runs"]), 1)

        listing = self.client.get("/cuenta/backtests").data.decode()
        self.assertIn("AAPL, MSFT, XLV", listing)
        run_id = runs[0]["id"]
        detail = self.client.get(f"/cuenta/backtests/{run_id}").data.decode()
        self.assertIn("Resultados por estrategia", detail)
        self.assertIn("Mensual", detail)
        self.post(f"/cuenta/backtests/{run_id}/eliminar")
        self.assertEqual(self.insforge.tables["backtest_runs"], [])
        self.assertEqual(self.insforge.tables["backtest_results"], [])

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_anonymous_backtest_is_not_recorded(self, download):
        clear_backtest_cache()
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        html = self._post_backtest()
        self.assertIn("para guardar tus backtests", html)
        self.assertNotIn("backtest_runs", self.insforge.tables)


class AccountsDisabledTests(WebTestCase):
    def test_pages_work_without_insforge(self):
        html = self.client.get("/").data.decode()
        self.assertNotIn("Ingresar", html)
        response = self.client.get("/auth/ingresar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.location).path, "/")


if __name__ == "__main__":
    unittest.main()
