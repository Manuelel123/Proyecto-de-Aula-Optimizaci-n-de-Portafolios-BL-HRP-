"""Copy the backtest history saved in InsForge into the local database.

Before accounts were local, backtests were stored in InsForge under the
InsForge user id. This importer maps each of those ids to its email (InsForge
auth), finds the local account with that email and copies the runs and their
results, skipping any run the account already has (same fingerprint).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import BacktestResult, BacktestRun
from optimizacion_portafolios.storage.users import get_user_by_email

RUN_FIELDS = (
    "fingerprint", "tickers", "start_date", "end_date", "trading_days", "lookback",
    "drift_threshold", "vol_window", "vol_threshold", "fees", "slippage", "init_cash",
    "best_strategy",
)
RESULT_FIELDS = (
    "strategy", "total_return", "cagr", "volatility", "sharpe", "sortino",
    "max_drawdown", "rebalances", "total_costs", "rebalance_dates",
)


@dataclass
class ImportReport:
    imported: dict[str, int] = field(default_factory=dict)  # local email -> runs copied
    duplicates: int = 0
    results: int = 0
    unmatched: dict[str, str] = field(default_factory=dict)  # InsForge email/id -> runs skipped


def insforge_emails(client) -> dict[str, str]:
    """InsForge user id -> email, from the auth users list (admin key)."""
    emails: dict[str, str] = {}
    offset = 0
    while True:
        page = client._request("GET", "/api/auth/users", params={"limit": 100, "offset": offset}) or {}
        users = page.get("data", []) if isinstance(page, dict) else page
        for user in users:
            if user.get("id") and user.get("email"):
                emails[str(user["id"])] = user["email"]
        if len(users) < 100:
            return emails
        offset += 100


def _json_list(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value or [])


def _timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def import_backtests(client, *, fallback_email: str | None = None, dry_run: bool = False) -> ImportReport:
    """Copy InsForge ``backtest_runs``/``backtest_results`` into local accounts.

    Runs whose InsForge owner has no local account go to ``fallback_email``
    when given; otherwise they are reported in ``unmatched`` and left out.
    """
    emails = insforge_emails(client)
    fallback = get_user_by_email(fallback_email) if fallback_email else None
    if fallback_email and fallback is None:
        raise ValueError(f"No existe una cuenta local con el correo {fallback_email}.")
    runs = client.select("backtest_runs", {"order": "created_at.asc"}, admin=True)
    results_by_run: dict[str, list[dict]] = {}
    for row in client.select("backtest_results", {}, admin=True):
        results_by_run.setdefault(str(row.get("run_id")), []).append(row)

    report = ImportReport()
    accounts: dict[str, object] = {}
    for run in runs:
        owner_id = str(run.get("user_id"))
        email = emails.get(owner_id)
        if owner_id not in accounts:
            accounts[owner_id] = (get_user_by_email(email) if email else None) or fallback
        user = accounts[owner_id]
        if user is None:
            label = email or owner_id
            report.unmatched[label] = report.unmatched.get(label, 0) + 1
            continue
        exists = db.session.query(BacktestRun.id).filter_by(
            user_id=str(user.id), fingerprint=run["fingerprint"]
        ).first()
        if exists:
            report.duplicates += 1
            continue
        local = BacktestRun(user_id=str(user.id), **{name: run.get(name) for name in RUN_FIELDS})
        local.tickers = _json_list(local.tickers)
        created_at = _timestamp(run.get("created_at"))
        if created_at is not None:
            local.created_at = created_at
        seen: set[str] = set()
        for row in results_by_run.get(str(run.get("id")), []):
            if row.get("strategy") in seen:
                continue
            seen.add(row.get("strategy"))
            result = BacktestResult(**{name: row.get(name) for name in RESULT_FIELDS})
            result.rebalance_dates = _json_list(result.rebalance_dates)
            local.results.append(result)
        report.results += len(local.results)
        report.imported[user.email] = report.imported.get(user.email, 0) + 1
        db.session.add(local)
    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()
    return report
