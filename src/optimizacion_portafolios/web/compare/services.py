"""Run HRP and Black-Litterman on the same assets and line up their results.

Both methods use the unchanged page services with the guided inputs (same
2-year window, SPY, neutral Black-Litterman views). The historical metrics
of both portfolios are measured with the same function (QuantStats) on the
same daily prices, so the figures are comparable.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
import requests
from yfinance.exceptions import YFException

from optimizacion_portafolios.analytics.performance import calculate_quantstats_metrics
from optimizacion_portafolios.data.market_data import download_prices
from optimizacion_portafolios.web.common import guided
from optimizacion_portafolios.web.hrp.services import WeightBounds, run_hrp

logger = logging.getLogger(__name__)

METHODS = ("hrp", "black_litterman")
METHOD_LABELS = {"hrp": "HRP", "black_litterman": "Black-Litterman"}
METRICS = (
    ("cagr", "Retorno anual compuesto", "Retorno anual compuesto"),
    ("volatility", "Volatilidad anualizada", "Volatilidad anualizada"),
    ("sharpe", "Ratio de Sharpe", "Ratio de Sharpe"),
    ("max_drawdown", "Máxima caída", "Caída máxima"),
)
_SIGNIFICANT = 0.01  # weights from 1 % count as "assets with weight"
_FAILURES = (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError)
_DOWNLOAD_FAILURES = (requests.RequestException, TimeoutError, YFException)


def _historical_metrics(weights: dict[str, float], start: date) -> dict:
    tickers = tuple(weights)
    prices = download_prices(tickers, start, date.today()).dropna(axis="columns", how="all")
    prices = prices.ffill().dropna()
    missing = [ticker for ticker in tickers if ticker not in prices.columns]
    if missing:
        raise ValueError("Sin precios para: " + ", ".join(missing))
    returns = prices.pct_change(fill_method=None).dropna()
    series = returns.loc[:, list(tickers)].dot(pd.Series(weights))
    values = calculate_quantstats_metrics(series)["Valor"]
    return {key: _number(values.get(source)) for key, source, _label in METRICS}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _run(method: str, tickers: list[str], max_weight: float, labels: dict[str, str]) -> dict:
    if method == "hrp":
        result = run_hrp(
            tickers,
            guided.GUIDED_BENCHMARK,
            guided.guided_start(),
            WeightBounds(max_weight=max_weight),
            labels,
        )
        weights = result["summary"]["weights"]
    else:
        result = guided.run_neutral_black_litterman(tickers, labels)
        weights = {row["ticker"]: row["weight"] for row in result["allocation_rows"]}
    return {"result": result, "weights": {ticker: float(value) for ticker, value in weights.items()}}


def compare(tickers: list[str], max_weight_pct: float, labels: dict[str, str]) -> dict:
    """Both portfolios, a weights table and a metrics table.

    A method that fails keeps its error message (``methods[m]["error"]``)
    without hiding the other one.
    """
    methods: dict[str, dict] = {}
    for method in METHODS:
        try:
            run = _run(method, tickers, max_weight_pct / 100, labels)
            run["metrics"] = _historical_metrics(run["weights"], guided.guided_start())
        except _DOWNLOAD_FAILURES as error:
            logger.exception("Price download failed during the comparison (%s)", method)
            run = {"error": f"No se pudieron descargar los datos: {error}"}
        except _FAILURES as error:
            run = {"error": str(error)}
        methods[method] = run

    ok = [method for method in METHODS if "weights" in methods[method]]
    assets = list(dict.fromkeys(ticker for method in ok for ticker in methods[method]["weights"]))
    rows = []
    for ticker in assets:
        hrp = methods["hrp"].get("weights", {}).get(ticker) if "hrp" in ok else None
        bl = methods["black_litterman"].get("weights", {}).get(ticker) if "black_litterman" in ok else None
        rows.append(
            {
                "ticker": ticker,
                "name": labels.get(ticker, ticker),
                "hrp": hrp,
                "black_litterman": bl,
                "difference": (bl - hrp) if hrp is not None and bl is not None else None,
            }
        )
    rows.sort(key=lambda row: -max(row["hrp"] or 0, row["black_litterman"] or 0))

    for method in ok:
        weights = methods[method]["weights"]
        methods[method]["max_weight"] = max(weights.values())
        methods[method]["max_ticker"] = max(weights, key=weights.get)
        methods[method]["assets_with_weight"] = sum(1 for value in weights.values() if value >= _SIGNIFICANT)

    metric_rows = [
        {
            "label": label,
            "key": key,
            "values": {method: methods[method].get("metrics", {}).get(key) for method in METHODS},
        }
        for key, _source, label in METRICS
    ]
    return {
        "methods": methods,
        "ok": ok,
        "rows": rows,
        "metric_rows": metric_rows,
        "labels": METHOD_LABELS,
        "start": guided.guided_start().isoformat(),
        "end": date.today().isoformat(),
    }
