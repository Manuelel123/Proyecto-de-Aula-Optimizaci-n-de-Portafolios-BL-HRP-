"""Saved portfolios and backtest history in InsForge, scoped to one user.

Every call takes the signed-in user's access token, so the owner-only RLS
policies of the tables decide which rows exist; ``user_id`` defaults to
``auth.uid()`` on insert.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from optimizacion_portafolios.insforge import InsForgeClient, InsForgeError, in_filter

PORTFOLIO_COLUMNS = (
    "id,name,model,benchmark,start_date,end_date,expected_return,volatility,"
    "sharpe,sortino,max_drawdown,params,created_at"
)
RUN_COLUMNS = (
    "id,tickers,start_date,end_date,trading_days,lookback,drift_threshold,"
    "vol_window,vol_threshold,fees,slippage,init_cash,best_strategy,created_at"
)
RESULT_COLUMNS = (
    "run_id,strategy,total_return,cagr,volatility,sharpe,sortino,max_drawdown,"
    "rebalances,total_costs,rebalance_dates"
)
LIST_LIMIT = 100


def _number(value: Any) -> float | None:
    """JSON-safe float (NaN and infinities become ``None``)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ---------------------------------------------------------------- Portfolios
def save_portfolio(client: InsForgeClient, token: str, snapshot: dict, name: str) -> str:
    """Store a portfolio snapshot and its weights; return the new id."""
    metrics = snapshot.get("metrics") or {}
    rows = client.insert(
        "saved_portfolios",
        [
            {
                "name": name,
                "model": snapshot["model"],
                "benchmark": snapshot.get("benchmark"),
                "start_date": snapshot.get("start_date"),
                "end_date": snapshot.get("end_date"),
                "expected_return": _number(metrics.get("expected_return")),
                "volatility": _number(metrics.get("volatility")),
                "sharpe": _number(metrics.get("sharpe")),
                "sortino": _number(metrics.get("sortino")),
                "max_drawdown": _number(metrics.get("max_drawdown")),
                "params": snapshot.get("params") or {},
            }
        ],
        token=token,
    )
    portfolio_id = rows[0]["id"]
    try:
        client.insert(
            "portfolio_weights",
            [
                {"portfolio_id": portfolio_id, "ticker": ticker, "weight": float(weight)}
                for ticker, weight in snapshot["weights"].items()
            ],
            token=token,
            returning=False,
        )
    except InsForgeError:
        client.delete("saved_portfolios", {"id": f"eq.{portfolio_id}"}, token=token)
        raise
    return portfolio_id


def _weights_by_portfolio(client: InsForgeClient, token: str, ids: list[str]) -> dict[str, list[dict]]:
    if not ids:
        return {}
    rows = client.select(
        "portfolio_weights",
        {"portfolio_id": in_filter(ids), "select": "portfolio_id,ticker,weight", "order": "weight.desc"},
        token=token,
    )
    grouped: dict[str, list[dict]] = {portfolio_id: [] for portfolio_id in ids}
    for row in rows:
        grouped.setdefault(row["portfolio_id"], []).append(row)
    return grouped


def list_portfolios(client: InsForgeClient, token: str) -> list[dict]:
    portfolios = client.select(
        "saved_portfolios",
        {"select": PORTFOLIO_COLUMNS, "order": "created_at.desc"},
        token=token,
        limit=LIST_LIMIT,
    )
    weights = _weights_by_portfolio(client, token, [row["id"] for row in portfolios])
    for portfolio in portfolios:
        portfolio["weights"] = weights.get(portfolio["id"], [])
    return portfolios


def get_portfolio(client: InsForgeClient, token: str, portfolio_id: str) -> dict | None:
    rows = client.select(
        "saved_portfolios",
        {"id": f"eq.{portfolio_id}", "select": PORTFOLIO_COLUMNS},
        token=token,
        limit=1,
    )
    if not rows:
        return None
    portfolio = rows[0]
    portfolio["weights"] = _weights_by_portfolio(client, token, [portfolio_id]).get(portfolio_id, [])
    return portfolio


def rename_portfolio(client: InsForgeClient, token: str, portfolio_id: str, name: str) -> bool:
    return bool(
        client.update("saved_portfolios", {"id": f"eq.{portfolio_id}"}, {"name": name}, token=token)
    )


def delete_portfolio(client: InsForgeClient, token: str, portfolio_id: str) -> bool:
    return bool(client.delete("saved_portfolios", {"id": f"eq.{portfolio_id}"}, token=token))


# ----------------------------------------------------------------- Backtests
def backtest_fingerprint(record: dict) -> str:
    """Stable hash of the inputs of a backtest run (same inputs, same run)."""
    inputs = {key: record.get(key) for key in ("tickers", "start_date", "end_date", "config", "bounds")}
    encoded = json.dumps(inputs, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_backtest(client: InsForgeClient, token: str, record: dict) -> tuple[str, bool]:
    """Store a backtest run once per distinct inputs; return ``(id, created)``."""
    fingerprint = backtest_fingerprint(record)
    existing = client.select(
        "backtest_runs", {"fingerprint": f"eq.{fingerprint}", "select": "id"}, token=token, limit=1
    )
    if existing:
        return existing[0]["id"], False
    config = record["config"]
    rows = client.insert(
        "backtest_runs",
        [
            {
                "fingerprint": fingerprint,
                "tickers": list(record["tickers"]),
                "start_date": record["start_date"],
                "end_date": record["end_date"],
                "trading_days": int(record["trading_days"]),
                "lookback": int(config["lookback"]),
                "drift_threshold": float(config["drift_threshold"]),
                "vol_window": int(config["vol_window"]),
                "vol_threshold": _number(config.get("vol_threshold")),
                "fees": float(config["fees"]),
                "slippage": float(config["slippage"]),
                "init_cash": float(config["init_cash"]),
                "best_strategy": record.get("best_strategy"),
            }
        ],
        token=token,
    )
    run_id = rows[0]["id"]
    try:
        client.insert(
            "backtest_results",
            [
                {
                    "run_id": run_id,
                    "strategy": strategy["strategy"],
                    "total_return": _number(strategy.get("total_return")),
                    "cagr": _number(strategy.get("cagr")),
                    "volatility": _number(strategy.get("volatility")),
                    "sharpe": _number(strategy.get("sharpe")),
                    "sortino": _number(strategy.get("sortino")),
                    "max_drawdown": _number(strategy.get("max_drawdown")),
                    "rebalances": strategy.get("rebalances"),
                    "total_costs": _number(strategy.get("total_costs")),
                    "rebalance_dates": strategy.get("rebalance_dates") or [],
                }
                for strategy in record["strategies"]
            ],
            token=token,
            returning=False,
        )
    except InsForgeError:
        client.delete("backtest_runs", {"id": f"eq.{run_id}"}, token=token)
        raise
    return run_id, True


def list_backtests(client: InsForgeClient, token: str) -> list[dict]:
    runs = client.select(
        "backtest_runs",
        {"select": RUN_COLUMNS, "order": "created_at.desc"},
        token=token,
        limit=LIST_LIMIT,
    )
    if not runs:
        return []
    results = client.select(
        "backtest_results",
        {
            "run_id": in_filter([run["id"] for run in runs]),
            "select": "run_id,strategy,total_return,sharpe",
        },
        token=token,
    )
    by_run: dict[str, list[dict]] = {}
    for row in results:
        by_run.setdefault(row["run_id"], []).append(row)
    for run in runs:
        rows = by_run.get(run["id"], [])
        best = next((row for row in rows if row["strategy"] == run.get("best_strategy")), None)
        run["best"] = best
        run["strategy_count"] = len(rows)
    return runs


def get_backtest(client: InsForgeClient, token: str, run_id: str) -> dict | None:
    rows = client.select(
        "backtest_runs", {"id": f"eq.{run_id}", "select": RUN_COLUMNS}, token=token, limit=1
    )
    if not rows:
        return None
    run = rows[0]
    run["results"] = client.select(
        "backtest_results",
        {"run_id": f"eq.{run_id}", "select": RESULT_COLUMNS, "order": "sharpe.desc.nullslast"},
        token=token,
    )
    return run


def delete_backtest(client: InsForgeClient, token: str, run_id: str) -> bool:
    return bool(client.delete("backtest_runs", {"id": f"eq.{run_id}"}, token=token))
