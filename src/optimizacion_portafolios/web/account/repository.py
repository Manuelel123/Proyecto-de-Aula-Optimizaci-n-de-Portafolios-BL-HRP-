"""HRP backtest history in the local database, scoped to one user.

Every function takes the ``user_id`` (``str(User.id)``) of the signed-in user,
so another user's run is simply not found.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from sqlalchemy import select

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import BacktestResult, BacktestRun

LIST_LIMIT = 100
_RUN_FIELDS = (
    "id", "tickers", "start_date", "end_date", "trading_days", "lookback", "drift_threshold",
    "vol_window", "vol_threshold", "fees", "slippage", "init_cash", "best_strategy", "created_at",
)
_RESULT_FIELDS = (
    "strategy", "total_return", "cagr", "volatility", "sharpe", "sortino", "max_drawdown",
    "rebalances", "total_costs", "rebalance_dates",
)


def _number(value: Any) -> float | None:
    """JSON-safe float (NaN and infinities become ``None``)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row(model, fields) -> dict:
    return {field: getattr(model, field) for field in fields}


def backtest_fingerprint(record: dict) -> str:
    """Stable hash of the inputs of a backtest run (same inputs, same run)."""
    inputs = {key: record.get(key) for key in ("tickers", "start_date", "end_date", "config", "bounds")}
    encoded = json.dumps(inputs, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _owned_run(user_id: str, run_id: str) -> BacktestRun | None:
    return db.session.scalar(
        select(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.user_id == user_id)
    )


def record_backtest(user_id: str, record: dict) -> tuple[str, bool]:
    """Store a backtest run once per distinct inputs; return ``(id, created)``."""
    fingerprint = backtest_fingerprint(record)
    existing = db.session.scalar(
        select(BacktestRun.id).where(
            BacktestRun.user_id == user_id, BacktestRun.fingerprint == fingerprint
        )
    )
    if existing:
        return existing, False
    config = record["config"]
    run = BacktestRun(
        user_id=user_id,
        fingerprint=fingerprint,
        tickers=list(record["tickers"]),
        start_date=record["start_date"],
        end_date=record["end_date"],
        trading_days=int(record["trading_days"]),
        lookback=int(config["lookback"]),
        drift_threshold=float(config["drift_threshold"]),
        vol_window=int(config["vol_window"]),
        vol_threshold=_number(config.get("vol_threshold")),
        fees=float(config["fees"]),
        slippage=float(config["slippage"]),
        init_cash=float(config["init_cash"]),
        best_strategy=record.get("best_strategy"),
        results=[
            BacktestResult(
                strategy=strategy["strategy"],
                total_return=_number(strategy.get("total_return")),
                cagr=_number(strategy.get("cagr")),
                volatility=_number(strategy.get("volatility")),
                sharpe=_number(strategy.get("sharpe")),
                sortino=_number(strategy.get("sortino")),
                max_drawdown=_number(strategy.get("max_drawdown")),
                rebalances=strategy.get("rebalances"),
                total_costs=_number(strategy.get("total_costs")),
                rebalance_dates=list(strategy.get("rebalance_dates") or []),
            )
            for strategy in record["strategies"]
        ],
    )
    db.session.add(run)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return run.id, True


def list_backtests(user_id: str) -> list[dict]:
    runs = db.session.scalars(
        select(BacktestRun)
        .where(BacktestRun.user_id == user_id)
        .order_by(BacktestRun.created_at.desc())
        .limit(LIST_LIMIT)
    ).all()
    listing = []
    for run in runs:
        row = _row(run, _RUN_FIELDS)
        best = next((result for result in run.results if result.strategy == run.best_strategy), None)
        row["best"] = _row(best, _RESULT_FIELDS) if best else None
        row["strategy_count"] = len(run.results)
        listing.append(row)
    return listing


def get_backtest(user_id: str, run_id: str) -> dict | None:
    run = _owned_run(user_id, run_id)
    if run is None:
        return None
    row = _row(run, _RUN_FIELDS)
    results = [_row(result, _RESULT_FIELDS) for result in run.results]
    # Sharpe descending, missing values last.
    results.sort(key=lambda result: (result["sharpe"] is None, -(result["sharpe"] or 0.0)))
    row["results"] = results
    return row


def delete_backtest(user_id: str, run_id: str) -> bool:
    run = _owned_run(user_id, run_id)
    if run is None:
        return False
    db.session.delete(run)
    db.session.commit()
    return True
