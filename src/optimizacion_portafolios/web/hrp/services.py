"""HRP workflow: download data, optimize and build the page view model."""

import re
from dataclasses import dataclass
from datetime import date

import pandas as pd
from skfolio.cluster import HierarchicalClustering

from optimizacion_portafolios.analytics.performance import (
    calculate_quantstats_metrics,
    generate_tearsheet,
)
from optimizacion_portafolios.analytics.volatility import calculate_monthly_volatility
from optimizacion_portafolios.data.market_data import download_prices
from optimizacion_portafolios.models.hrp import (
    WeightBounds,
    calculate_hrp_contributions,
    optimize_hrp,
)
from optimizacion_portafolios.web.common.charts import (
    allocation_chart,
    contribution_chart,
    correlation_heatmap,
    dendrogram_chart,
    historical_charts,
    returns_box_chart,
    volatility_histogram_chart,
)
from optimizacion_portafolios.web.common.price_series import price_series_payload
from optimizacion_portafolios.web.common.tables import (
    dataframe_html,
    format_percent,
    metrics_html,
)

ROLLING_SHARPE_PERIOD = 126
REPORT_FILENAME = "reporte_quantstats_hrp.html"
_PORTFOLIO_COLUMN = "Portafolio HRP"
_QUANTSTATS_SORTINO = "Ratio de Sortino"
_QUANTSTATS_SORTINO_LABEL = (
    "Ratio de Sortino (QuantStats · días comunes con el benchmark)"
)
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class HrpRun:
    raw_prices: pd.DataFrame  # downloaded prices, before ffill/alignment
    prices: pd.DataFrame
    returns: pd.DataFrame
    weights: pd.Series
    ordered_correlation: pd.DataFrame
    clustering: HierarchicalClustering | None
    sortino_ratio: float
    portfolio_returns: pd.Series
    benchmark_returns: pd.Series
    benchmark_prices: pd.Series
    missing: list[str]


def _optimize(
    tickers: list[str], benchmark: str, start_date: date, bounds: WeightBounds
) -> HrpRun:
    today = date.today()
    raw_prices = download_prices(tuple(tickers), start_date, today)
    raw_prices = raw_prices.dropna(axis="columns", how="all")
    prices = raw_prices.ffill().dropna()
    if prices.shape[1] < 2:
        raise ValueError(
            "Yahoo Finance devolvió datos para menos de dos activos seleccionados."
        )
    missing = sorted(set(tickers) - set(prices.columns))
    returns = prices.pct_change(fill_method=None).dropna(how="all").dropna()
    if len(returns) < 2:
        raise ValueError("Se necesitan más observaciones para optimizar HRP.")

    hrp = optimize_hrp(returns, bounds.restricted_to(list(returns.columns)))
    weights = hrp.weights
    benchmark_prices = download_prices((benchmark,), start_date, today)
    if benchmark_prices.empty or benchmark not in benchmark_prices:
        raise ValueError(
            f"Yahoo Finance no devolvió datos para el benchmark {benchmark}."
        )
    benchmark_series = benchmark_prices[benchmark].rename(benchmark)
    benchmark_returns = benchmark_series.pct_change(fill_method=None)
    portfolio_returns = returns.dot(weights).rename(_PORTFOLIO_COLUMN)
    aligned = pd.concat(
        [portfolio_returns, benchmark_returns], axis=1, sort=False
    ).dropna()
    if aligned.empty:
        raise ValueError(
            f"No hay retornos coincidentes entre el portafolio y el benchmark {benchmark}."
        )
    return HrpRun(
        raw_prices=raw_prices.loc[:, list(prices.columns)],
        prices=prices,
        returns=returns,
        weights=weights,
        ordered_correlation=hrp.ordered_correlation,
        clustering=hrp.clustering,
        sortino_ratio=hrp.sortino_ratio,
        portfolio_returns=aligned[_PORTFOLIO_COLUMN],
        benchmark_returns=aligned[benchmark],
        benchmark_prices=benchmark_series,
        missing=missing,
    )


def build_hrp_report(
    tickers: list[str],
    benchmark: str,
    start_date: date,
    bounds: WeightBounds | None = None,
) -> tuple[bytes, list[str]]:
    """Return the QuantStats HTML tearsheet and the tickers without data."""
    run = _optimize(tickers, benchmark, start_date, bounds or WeightBounds())
    report = generate_tearsheet(
        run.portfolio_returns,
        run.benchmark_returns,
        _PORTFOLIO_COLUMN,
        REPORT_FILENAME,
    )
    return report, run.missing


def asset_name(ticker: str, label: str | None) -> str:
    """Readable asset name from a catalog label ("Apple (AAPL)" -> "Apple").

    Returns "—" when the catalog has no name besides the ticker itself.
    """
    if not label:
        return "—"
    name = re.sub(r"\s*\(" + re.escape(ticker) + r"\)\s*$", "", label).strip()
    return name if name and name != ticker else "—"


def has_weight_limits(bounds: WeightBounds) -> bool:
    """True when the bounds differ from the unconstrained 0 %–100 % default."""
    return bool(
        bounds.min_weight > _TOLERANCE
        or bounds.max_weight < 1 - _TOLERANCE
        or bounds.asset_min_weights
        or bounds.asset_max_weights
    )


def _allocation_rows(
    weights: pd.Series,
    names: dict[str, str],
    limits: dict[str, tuple[float, float]] | None,
) -> list[dict]:
    rows = []
    for ticker, weight in weights.sort_values(ascending=False).items():
        row = {
            "ticker": ticker,
            "name": names[ticker],
            "weight": float(weight),
            "weight_text": format_percent(weight),
        }
        if limits:
            low, high = limits[ticker]
            row["limit_text"] = f"{low:.2%} – {high:.2%}"
            row["at_limit"] = abs(weight - low) < 1e-6 or abs(weight - high) < 1e-6
        rows.append(row)
    return rows


def run_hrp(
    tickers: list[str],
    benchmark: str,
    start_date: date,
    bounds: WeightBounds | None = None,
    labels: dict[str, str] | None = None,
) -> dict:
    """Optimize the portfolio and return everything the HRP page renders.

    ``labels`` maps tickers to catalog labels (e.g. "AAPL" -> "Apple (AAPL)").
    """
    bounds = bounds or WeightBounds()
    labels = labels or {}
    run = _optimize(tickers, benchmark, start_date, bounds)
    returns, weights = run.returns, run.weights
    assets = list(returns.columns)
    names = {ticker: asset_name(ticker, labels.get(ticker)) for ticker in assets}
    chart_names = {ticker: labels.get(ticker) or ticker for ticker in assets}

    has_limits = has_weight_limits(bounds)
    limits = None
    if has_limits:
        minimums, maximums = bounds.restricted_to(assets).resolve(assets)
        limits = {
            ticker: (float(minimums[ticker]), float(maximums[ticker]))
            for ticker in assets
        }

    _, contributions = calculate_hrp_contributions(returns, weights)
    contributions = contributions.rename_axis("Activo")
    history_charts, has_rolling_sharpe = historical_charts(
        run.portfolio_returns,
        run.benchmark_returns,
        rolling_period=ROLLING_SHARPE_PERIOD,
    )
    monthly_volatility = calculate_monthly_volatility(returns)
    charts = {
        "allocation": allocation_chart(
            weights, chart_names, reference=1 / len(weights), bounds=limits
        ),
        **history_charts,
        "correlation": correlation_heatmap(
            run.ordered_correlation, "Correlación cuasi-diagonal"
        ),
        "dendrogram": dendrogram_chart(run.clustering),
        "risk_contribution": contribution_chart(
            contributions, "Contribución al riesgo"
        ),
        "return_contribution": contribution_chart(
            contributions, "Participación del retorno"
        ),
        "monthly_volatility": (
            volatility_histogram_chart(
                monthly_volatility, monthly_volatility.ffill().iloc[-1]
            )
            if not monthly_volatility.empty
            else None
        ),
        "returns_box": returns_box_chart(returns),
    }
    risk_shares = contributions["Contribución al riesgo"]
    top_risk = str(risk_shares.idxmax()) if risk_shares.notna().any() else None

    return {
        "charts": {key: value for key, value in charts.items() if value is not None},
        "prices_payload": price_series_payload(
            run.raw_prices, chart_names, run.benchmark_prices
        ),
        "allocation_rows": _allocation_rows(weights, names, limits),
        "has_limits": has_limits,
        "tables": {
            "contributions": dataframe_html(
                contributions,
                {column: format_percent for column in contributions.columns},
            ),
            "metrics": metrics_html(
                calculate_quantstats_metrics(run.portfolio_returns).rename(
                    index={_QUANTSTATS_SORTINO: _QUANTSTATS_SORTINO_LABEL}
                )
            ),
            "correlation": dataframe_html(
                run.ordered_correlation,
                {
                    column: lambda value: f"{value:.2f}"
                    for column in run.ordered_correlation
                },
            ),
        },
        "start": run.prices.index.min().date().isoformat(),
        "end": run.prices.index.max().date().isoformat(),
        "observations": len(returns),
        "common_observations": len(run.portfolio_returns),
        "weight_sum": float(weights.sum()),
        "min_assigned_weight": float(weights.min()),
        "max_assigned_weight": float(weights.max()),
        "min_weight_ticker": str(weights.idxmin()),
        "max_weight_ticker": str(weights.idxmax()),
        "equal_weight": 1 / len(weights),
        "top_risk_ticker": top_risk,
        "top_risk_share": float(risk_shares[top_risk]) if top_risk else None,
        "sortino_ratio": run.sortino_ratio,
        "missing": run.missing,
        "has_rolling_sharpe": has_rolling_sharpe,
        "selected_tickers": assets,
        "benchmark": benchmark,
    }
