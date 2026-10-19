"""F1: storage layer, ownership checks, Flask-Login and the sign-in throttle."""

import unittest
from datetime import date
from urllib.parse import urlsplit

from optimizacion_portafolios.storage import db, repositories
from optimizacion_portafolios.storage.models import Holding, PortfolioVersion
from optimizacion_portafolios.storage.repositories import HoldingData, TransactionData
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.auth.throttle import LoginThrottle
from test_web_accounts import AccountTestCase


def create_sample(user_id: str, name: str = "Mi óptimo"):
    return repositories.create_portfolio(
        user_id,
        name=name,
        model="hrp",
        benchmark="SPY",
        initial_capital=100_000,
        tracking_start=date(2026, 1, 5),
        holdings=[
            HoldingData("AAPL", 0.6, 300.0, 200.0),
            HoldingData("MSFT", 0.4, 100.0, 400.0),
        ],
        transactions=[
            TransactionData("AAPL", "buy", 300.0, 200.0, 60_000),
            TransactionData("MSFT", "buy", 100.0, 400.0, 40_000),
        ],
        params={"universe": "Portafolio actual"},
    )


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "x"})
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def test_create_portfolio_stores_version_one_with_holdings(self):
        portfolio = create_sample("user-a")
        version = portfolio.current_version
        self.assertEqual((version.number, version.reason), (1, "fix"))
        self.assertEqual(version.target_weights, {"AAPL": 0.6, "MSFT": 0.4})
        self.assertEqual(version.units["AAPL"], 300.0)
        self.assertEqual(len(version.transactions), 2)
        self.assertEqual(version.params["universe"], "Portafolio actual")

    def test_reads_are_scoped_to_the_owner(self):
        mine = create_sample("user-a")
        create_sample("user-b", "Ajeno")
        self.assertEqual([p.name for p in repositories.list_portfolios("user-a")], ["Mi óptimo"])
        self.assertIsNotNone(repositories.get_portfolio("user-a", mine.id))
        self.assertIsNone(repositories.get_portfolio("user-b", mine.id))

    def test_versions_are_numbered_and_cannot_go_back_in_time(self):
        portfolio = create_sample("user-a")
        version = repositories.add_version(
            portfolio, effective_date=date(2026, 2, 2), reason="rebalance",
            holdings=[HoldingData("AAPL", 0.5, 240.0, 210.0), HoldingData("MSFT", 0.5, 120.0, 420.0)],
            transactions=[], value_before=100_800, value_after=100_700, cost=100, turnover=0.1,
        )
        self.assertEqual(version.number, 2)
        self.assertEqual(portfolio.current_version.target_weights["AAPL"], 0.5)
        with self.assertRaises(ValueError):
            repositories.add_version(
                portfolio, effective_date=date(2026, 1, 1), reason="rebalance",
                holdings=[HoldingData("AAPL", 1.0, 1.0, 1.0)], transactions=[],
                value_before=1, value_after=1, cost=0, turnover=0,
            )

    def test_deleting_a_portfolio_cascades(self):
        portfolio = create_sample("user-a")
        db.session.delete(portfolio)
        db.session.commit()
        self.assertEqual(db.session.query(PortfolioVersion).count(), 0)
        self.assertEqual(db.session.query(Holding).count(), 0)

    def test_archive_keeps_history(self):
        portfolio = create_sample("user-a")
        repositories.archive_portfolio(portfolio, None)
        self.assertEqual(repositories.active_portfolios(), [])
        self.assertEqual(len(repositories.list_portfolios("user-a")), 1)


class PortfolioPagesTests(AccountTestCase):
    def _own_id(self):
        return self.user_id_of()

    def test_pages_require_sign_in(self):
        response = self.client.get("/portafolios/")
        self.assertEqual(urlsplit(response.location).path, "/auth/ingresar")

    def test_signed_in_user_sees_nav_link_and_empty_state(self):
        self.assertNotIn("Mis portafolios", self.client.get("/").data.decode())
        self.sign_in()
        html = self.client.get("/portafolios/").data.decode()
        self.assertIn("Aún no tienes portafolios en seguimiento", html)
        self.assertIn('href="/portafolios/"', self.client.get("/").data.decode())
        self.assertIn("Capital nocional", html)

    def test_owner_sees_portfolio_and_others_get_404(self):
        self.sign_in()
        with self.app.app_context():
            mine = create_sample(self._own_id()).id
            other = create_sample("someone-else", "Ajeno").id
        html = self.client.get(f"/portafolios/{mine}").data.decode()
        self.assertIn("Mi óptimo", html)
        self.assertIn("AAPL", html)
        self.assertEqual(self.client.get(f"/portafolios/{other}").status_code, 404)
        self.assertNotIn("Ajeno", self.client.get("/portafolios/").data.decode())

    def test_flask_login_tracks_the_local_account(self):
        self.sign_in()
        with self.client.session_transaction() as session:
            self.assertEqual(session["_user_id"], self.user_id_of())
        self.post("/auth/salir")
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)


class ThrottleTests(unittest.TestCase):
    def test_window_and_reset(self):
        throttle = LoginThrottle(max_failures=2, window=60)
        throttle.record_failure("email:a")
        self.assertEqual(throttle.retry_after("email:a"), 0)
        throttle.record_failure("email:a")
        self.assertGreater(throttle.retry_after("email:a", "ip:1"), 0)
        throttle.reset("email:a")
        self.assertEqual(throttle.retry_after("email:a"), 0)


if __name__ == "__main__":
    unittest.main()
