"""Mis portafolios workflows: fix a model result and value it over time."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import requests
from yfinance.exceptions import YFException

from optimizacion_portafolios.analytics.tracking import (
    Segment,
    current_weights,
    holding_values,
    initial_positions,
    last_prices,
    rebased,
    tracking_summary,
    weight_drift,
)
from optimizacion_portafolios.data.catalogs import BENCHMARKS
from optimizacion_portafolios.data.fx import prices_in_usd
from optimizacion_portafolios.data.market_data import download_prices
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.models import Portfolio
from optimizacion_portafolios.storage.repositories import HoldingData, TransactionData
from optimizacion_portafolios.web.common.charts import tracking_value_chart, weights_vs_target_chart
from optimizacion_portafolios.web.portfolios.snapshots import MODEL_LABELS, default_name

logger = logging.getLogger(__name__)

DEFAULT_CAPITAL = 100_000.0
DEFAULT_COMMISSION_BPS = 10.0
# Calendar days searched backwards for the closing prices used to fix a portfolio.
FIX_PRICE_LOOKBACK_DAYS = 14
DOWNLOAD_ERRORS = (requests.RequestException, TimeoutError, YFException)
STATUS_LABELS = {"active": "Activo", "archived": "Archivado"}
REASON_LABELS = {
    "fix": "Fijado como óptimo",
    "rebalance": "Rebalanceo",
    "reoptimize": "Re-optimización",
    "remove": "Eliminación de activos",
    "add": "Alta de activos",
}
_BENCHMARK_LABELS = {ticker: label for label, ticker in BENCHMARKS.items()}


def benchmark_options(current: str) -> list[tuple[str, str]]:
    """``(ticker, label)`` choices of the fixing form (the result's own first)."""
    options = [(ticker, label) for label, ticker in BENCHMARKS.items()]
    if current and current not in _BENCHMARK_LABELS:
        options.insert(0, (current, current))
    return options


def fix_form(snapshot: dict) -> dict:
    """Template data of the "Establecer como portafolio óptimo" form."""
    weights = snapshot["weights"]
    return {
        "model": MODEL_LABELS[snapshot["model"]],
        "default_name": default_name(snapshot),
        "capital": DEFAULT_CAPITAL,
        "commission_bps": DEFAULT_COMMISSION_BPS,
        "benchmark": snapshot.get("benchmark") or "SPY",
        "benchmarks": benchmark_options(snapshot.get("benchmark") or ""),
        "weights": sorted(weights.items(), key=lambda item: -item[1]),
    }


def _usd_prices(tickers: tuple[str, ...], start: date, end: date) -> pd.DataFrame:
    raw = download_prices(tickers, start, end)
    available = [ticker for ticker in tickers if ticker in raw.columns and raw[ticker].notna().any()]
    return prices_in_usd(raw.loc[:, available], start, end) if available else pd.DataFrame()


def fix_portfolio(
    user_id: str,
    snapshot: dict,
    *,
    name: str,
    capital: float,
    benchmark: str,
    commission_bps: float,
    today: date | None = None,
) -> Portfolio:
    """Store the snapshot as version 1, bought at the latest available closes.

    Raises ``ValueError`` (Spanish message) when a ticker or the benchmark has
    no recent price.
    """
    today = today or date.today()
    weights = {ticker: float(weight) for ticker, weight in snapshot["weights"].items()}
    tickers = tuple(weights)
    start = today - timedelta(days=FIX_PRICE_LOOKBACK_DAYS)
    try:
        prices = _usd_prices(tickers, start, today)
        benchmark_prices = download_prices((benchmark,), start, today)
    except DOWNLOAD_ERRORS as error:
        raise ValueError(f"No se pudieron descargar los precios de fijación: {error}") from error
    missing = [ticker for ticker in tickers if ticker not in prices.columns]
    if missing:
        raise ValueError("No hay precios recientes para: " + ", ".join(missing))
    if benchmark not in benchmark_prices or benchmark_prices[benchmark].dropna().empty:
        raise ValueError(f"No hay precios recientes para el benchmark {benchmark}.")
    price_day, closes = last_prices(prices)
    positions = initial_positions(capital, weights, closes, commission_bps)
    total_weight = sum(weights.values())
    holdings = [
        HoldingData(ticker, weights[ticker] / total_weight, positions.units[ticker], closes[ticker])
        for ticker in tickers
    ]
    transactions = [
        TransactionData(
            ticker,
            "buy" if positions.units[ticker] >= 0 else "sell",
            abs(positions.units[ticker]),
            closes[ticker],
            abs(positions.amounts[ticker]),
            positions.cost * abs(weights[ticker]) / sum(abs(w) for w in weights.values()),
        )
        for ticker in tickers
    ]
    params = {
        **(snapshot.get("params") or {}),
        "metrics_at_fixing": snapshot.get("metrics") or {},
        "price_date": price_day.date().isoformat(),
        "currency": "USD",
    }
    return repositories.create_portfolio(
        user_id,
        name=name,
        model=snapshot["model"],
        benchmark=benchmark,
        initial_capital=capital,
        tracking_start=price_day.date(),
        holdings=holdings,
        transactions=transactions,
        params=params,
        commission_bps=commission_bps,
        cost=positions.cost,
    )


# ------------------------------------------------------------------ list/detail
def _summary(portfolio: Portfolio) -> dict:
    version = portfolio.current_version
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "model": MODEL_LABELS.get(portfolio.model, portfolio.model),
        "status": portfolio.status,
        "status_label": STATUS_LABELS.get(portfolio.status, portfolio.status),
        "benchmark": portfolio.benchmark,
        "benchmark_label": _BENCHMARK_LABELS.get(portfolio.benchmark, portfolio.benchmark),
        "initial_capital": portfolio.initial_capital,
        "commission_bps": portfolio.commission_bps,
        "tracking_start": portfolio.tracking_start.isoformat(),
        "version_number": version.number if version else 0,
        "asset_count": len(version.holdings) if version else 0,
        "tickers": [holding.ticker for holding in version.holdings] if version else [],
    }


def portfolio_list(user_id: str) -> list[dict]:
    return [_summary(portfolio) for portfolio in repositories.list_portfolios(user_id)]


def segments(portfolio: Portfolio) -> list[Segment]:
    return [
        Segment(pd.Timestamp(version.effective_date), version.units, REASON_LABELS.get(version.reason, version.reason))
        for version in portfolio.versions
    ]


def valuation(portfolio: Portfolio, today: date | None = None) -> dict:
    """Daily buy-and-hold valuation since the fixing date (USD).

    Returns ``values`` (per-asset frame), ``total``, ``benchmark`` (rebased to
    the initial capital) and the prices used; raises ``ValueError``.
    """
    end = today or date.today()
    start = portfolio.tracking_start
    tickers = tuple(sorted({h.ticker for version in portfolio.versions for h in version.holdings}))
    try:
        prices = _usd_prices(tickers, start, end)
        benchmark = _usd_prices((portfolio.benchmark,), start, end)
    except DOWNLOAD_ERRORS as error:
        raise ValueError(f"No se pudieron descargar los precios: {error}") from error
    values = holding_values(prices, segments(portfolio))
    total = values.sum(axis=1).rename("Portafolio")
    benchmark_series = (
        rebased(benchmark[portfolio.benchmark], portfolio.initial_capital, pd.Timestamp(start))
        if portfolio.benchmark in benchmark.columns
        else pd.Series(dtype=float)
    )
    return {"values": values, "total": total, "benchmark": benchmark_series, "prices": prices}


def portfolio_detail(portfolio: Portfolio, today: date | None = None) -> dict:
    version = portfolio.current_version
    view = {
        **_summary(portfolio),
        "version": {
            "number": version.number,
            "effective_date": version.effective_date.isoformat(),
            "reason": REASON_LABELS.get(version.reason, version.reason),
            "value_after": version.value_after,
            "cost": version.cost,
        },
        "params": version.params or {},
        "tracking": None,
        "tracking_error": None,
    }
    try:
        valued = valuation(portfolio, today)
    except ValueError as error:
        view["tracking_error"] = str(error)
        valued = None
    target = version.target_weights
    if valued is not None and not valued["total"].empty:
        values, total = valued["values"], valued["total"]
        current = current_weights(values)
        drift = weight_drift(current, target)
        last_close = valued["prices"].ffill().iloc[-1]
        view["tracking"] = {
            **tracking_summary(total, valued["benchmark"], portfolio.initial_capital),
            "charts": {
                key: chart
                for key, chart in {
                    "value": tracking_value_chart(
                        total,
                        valued["benchmark"],
                        [(v.effective_date, REASON_LABELS.get(v.reason, v.reason)) for v in portfolio.versions[1:]],
                        benchmark_name=_BENCHMARK_LABELS.get(portfolio.benchmark, portfolio.benchmark),
                    ),
                    "weights": weights_vs_target_chart(current, target),
                }.items()
                if chart is not None
            },
        }
    else:
        current, drift, last_close = pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)
    view["holdings"] = [
        {
            "ticker": holding.ticker,
            "target_weight": holding.target_weight,
            "current_weight": float(current.get(holding.ticker, float("nan"))) if not current.empty else None,
            "drift": float(drift.get(holding.ticker, float("nan"))) if not drift.empty else None,
            "units": holding.units,
            "entry_price": holding.entry_price,
            "last_price": float(last_close.get(holding.ticker)) if holding.ticker in last_close else None,
        }
        for holding in version.holdings
    ]
    for row in view["holdings"]:
        row["value"] = row["units"] * row["last_price"] if row["last_price"] is not None else None
    return view
