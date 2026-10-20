"""Importing the InsForge backtest history into local accounts."""

from unittest import mock

from fake_insforge import FakeInsForge
from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import BacktestRun
from test_web_accounts import AccountTestCase

ANA_ID = "04848458-aaaa"
LUIS_ID = "bcf5c04a-bbbb"


class FakeWithUsers(FakeInsForge):
    def __init__(self):
        super().__init__()
        self.users = [{"id": ANA_ID, "email": "Ana@example.com"}, {"id": LUIS_ID, "email": "luis@example.com"}]

    def _request(self, method, path, *, params=None, json=None, headers=None):
        assert (method, path) == ("GET", "/api/auth/users")
        offset = params.get("offset", 0)
        return {"data": self.users[offset:offset + params["limit"]], "pagination": {}}


def run(run_id, owner, fingerprint):
    return {
        "id": run_id, "user_id": owner, "fingerprint": fingerprint, "tickers": ["AAPL", "MSFT"],
        "start_date": "2025-01-02", "end_date": "2026-01-02", "trading_days": 252, "lookback": 252,
        "drift_threshold": 0.05, "vol_window": 63, "vol_threshold": None, "fees": 0.001,
        "slippage": 0.0005, "init_cash": 10000, "best_strategy": "Trimestral",
        "created_at": "2026-10-15T03:38:31.251416+00:00",
    }


def result(run_id, strategy):
    return {
        "run_id": run_id, "strategy": strategy, "total_return": 0.13, "cagr": 0.13, "volatility": 0.1,
        "sharpe": 1.2, "sortino": 1.8, "max_drawdown": -0.07, "rebalances": 2, "total_costs": 15.0,
        "rebalance_dates": '["2025-04-01", "2025-07-01"]',
    }


class ImportTests(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeWithUsers()
        self.fake.tables["backtest_runs"] = [run("r1", ANA_ID, "f1"), run("r2", LUIS_ID, "f2")]
        self.fake.tables["backtest_results"] = [result("r1", "Buy & hold"), result("r1", "Trimestral"), result("r2", "Buy & hold")]

    def invoke(self, *args):
        with mock.patch("optimizacion_portafolios.web.auth.cli._insforge_client", return_value=self.fake):
            return self.app.test_cli_runner().invoke(args=["users", "import-insforge-backtests", *args])

    def runs(self):
        with self.app.app_context():
            return [(run.user_id, run.fingerprint, len(run.results), run.results[0].rebalance_dates)
                    for run in db.session.query(BacktestRun).order_by(BacktestRun.fingerprint)]

    def test_copies_runs_to_the_account_with_the_same_email_once(self):
        ana = self.create_user("ana@example.com")
        output = self.invoke().output
        self.assertIn("ana@example.com: 1 backtests copiados", output)
        self.assertIn("luis@example.com: 1 backtests omitidos", output)
        self.assertEqual(self.runs(), [(ana, "f1", 2, ["2025-04-01", "2025-07-01"])])
        again = self.invoke().output
        self.assertIn("1 ya existían", again)
        self.assertEqual(len(self.runs()), 1)

    def test_fallback_account_receives_unmatched_runs(self):
        ana = self.create_user("ana@example.com")
        self.invoke("--fallback", "ana@example.com")
        self.assertEqual([row[:3] for row in self.runs()], [(ana, "f1", 2), (ana, "f2", 1)])

    def test_dry_run_saves_nothing(self):
        self.create_user("ana@example.com")
        output = self.invoke("--dry-run").output
        self.assertIn("[simulación] ana@example.com: 1 backtests copiados", output)
        self.assertEqual(self.runs(), [])

    def test_unknown_fallback_is_an_error(self):
        result = self.invoke("--fallback", "nadie@example.com")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No existe una cuenta local", result.output)
