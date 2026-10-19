"""HRP workflow: download data, optimize and build the page view model."""

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests
from skfolio.cluster import HierarchicalClustering
from yfinance.exceptions import YFException

from optimizacion_portafolios.analytics.performance import (
    calculate_quantstats_metrics,
    generate_tearsheet,
)
from optimizacion_portafolios.analytics.volatility import calculate_monthly_volatility
from optimizacion_portafolios.data.catalogs import BENCHMARKS
from optimizacion_portafolios.data.market_data import download_prices
from optimizacion_portafolios.models.hrp import (
    WeightBounds,
    calculate_hrp_contributions,
    optimize_hrp,
)
from optimizacion_portafolios.models.hrp_backtest import (
    BUY_AND_HOLD,
    BacktestConfig,
    BacktestResult,
    cached_backtest,
)
from optimizacion_portafolios.web.common.charts import (
    PORTFOLIO_SERIES,
    allocation_chart,
    contribution_chart,
    correlation_heatmap,
    dendrogram_chart,
    historical_charts,
    returns_box_chart,
    returns_matrix_chart,
    strategy_drawdown_chart,
    strategy_equity_chart,
    volatility_histogram_chart,
)
from optimizacion_portafolios.web.common.price_series import price_series_payload
from optimizacion_portafolios.web.common.tables import (
    dataframe_html,
    format_decimal,
    format_percent,
    metrics_html,
)

logger = logging.getLogger(__name__)

ROLLING_SHARPE_PERIOD = 126
REPORT_FILENAME = "reporte_quantstats_hrp.html"
_PORTFOLIO_COLUMN = "Portafolio HRP"
_QUANTSTATS_SORTINO = "Ratio de Sortino"
_QUANTSTATS_SORTINO_LABEL = (
    "Ratio de Sortino (QuantStats · días comunes con el benchmark)"
)
_TOLERANCE = 1e-9
_BENCHMARK_LABELS = {ticker: label for label, ticker in BENCHMARKS.items()}
# Calendar days requested per trading day of the estimation window (weekends,
# holidays) plus a fixed margin, so the backtest can start on ``start_date``.
_CALENDAR_DAYS_PER_TRADING_DAY = 1.5
_HISTORY_MARGIN_DAYS = 14
_BACKTEST_PERCENT_COLUMNS = (
    "Retorno total",
    "CAGR",
    "Volatilidad anualizada",
    "Máxima caída",
    "Costos / capital inicial",
)
_BACKTEST_RATIO_COLUMNS = ("Ratio de Sharpe", "Ratio de Sortino")
_BACKTEST_COUNT_COLUMNS = ("Rebalanceos", "Órdenes")
_BACKTEST_MONEY_COLUMNS = ("Comisiones", "Costo de slippage", "Costos totales")


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


def _daily_returns(prices: pd.Series) -> pd.Series:
    """Daily simple returns of one asset over its own quotes (no ffill)."""
    return prices.dropna().pct_change(fill_method=None).dropna()


def _returns_matrix(run: HrpRun, benchmark: str, chart_names: dict[str, str]):
    """Returns matrix: portfolio, benchmark (unless it is an asset), assets.

    Assets follow the allocation order (weight, descending) and use their own
    price history, so different start dates and crypto weekends are kept.
    """
    series = {PORTFOLIO_SERIES: run.returns.dot(run.weights)}
    names = {PORTFOLIO_SERIES: _PORTFOLIO_COLUMN, **chart_names}
    if benchmark not in run.weights.index:
        series[benchmark] = _daily_returns(run.benchmark_prices)
        names[benchmark] = _BENCHMARK_LABELS.get(benchmark, benchmark)
    for ticker in run.weights.sort_values(ascending=False).index:
        series[ticker] = _daily_returns(run.raw_prices[ticker])
    return returns_matrix_chart(series, names)


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


def _backtest_prices(
    assets: list[str], start_date: date, config: BacktestConfig
) -> pd.DataFrame:
    """Aligned prices from ``lookback`` trading days before ``start_date``."""
    history_start = start_date - timedelta(
        days=int(config.lookback * _CALENDAR_DAYS_PER_TRADING_DAY)
        + _HISTORY_MARGIN_DAYS
    )
    raw = download_prices(tuple(assets), history_start, date.today())
    available = [asset for asset in assets if asset in raw.columns]
    prices = raw.loc[:, available].dropna(axis="columns", how="all").ffill().dropna()
    missing = [asset for asset in assets if asset not in prices.columns]
    if missing:
        raise ValueError(
            "No hay historia de precios para el backtest de: " + ", ".join(missing)
        )
    return prices


def _money(value) -> str:
    return "N/D" if pd.isna(value) else f"${value:,.2f}"


def _backtest_metrics_table(metrics: pd.DataFrame) -> str:
    formatters = {}
    for column in metrics.columns:
        if column in _BACKTEST_PERCENT_COLUMNS:
            formatters[column] = format_percent
        elif column in _BACKTEST_RATIO_COLUMNS:
            formatters[column] = lambda value: "N/D" if pd.isna(value) else f"{value:.3f}"
        elif column in _BACKTEST_COUNT_COLUMNS:
            formatters[column] = lambda value: f"{int(value):d}"
        elif column in _BACKTEST_MONEY_COLUMNS:
            formatters[column] = _money
        else:
            formatters[column] = format_decimal
    return dataframe_html(metrics, formatters)


def backtest_view(
    result: BacktestResult, from_cache: bool = False, seconds: float | None = None
) -> dict:
    """Template data of the "Backtest de rebalanceo" section."""
    reference = result.labels.get(BUY_AND_HOLD)
    metrics = result.metrics
    sharpe = metrics["Ratio de Sharpe"].dropna()
    best = str(sharpe.idxmax()) if not sharpe.empty else None
    config = result.config
    return {
        "charts": {
            key: value
            for key, value in {
                "equity": strategy_equity_chart(result.values, reference),
                "drawdown": strategy_drawdown_chart(result.values, reference),
            }.items()
            if value is not None
        },
        "metrics_table": _backtest_metrics_table(metrics),
        "rebalances": [
            {
                "strategy": label,
                "count": len(dates),
                "dates": [day.date().isoformat() for day in dates],
            }
            for label, dates in result.rebalance_dates.items()
        ],
        "best_strategy": best,
        "best_sharpe": float(sharpe[best]) if best else None,
        "best_return_strategy": str(metrics["Retorno total"].idxmax()),
        "start": result.start.date().isoformat(),
        "end": result.end.date().isoformat(),
        "days": len(result.values),
        "lookback": config.lookback,
        "drift_threshold": config.drift_threshold,
        "vol_window": config.vol_window,
        "vol_threshold": config.vol_threshold,
        "fees": config.fees,
        "slippage": config.slippage,
        "init_cash": config.init_cash,
        "from_cache": from_cache,
        "seconds": seconds,
        "record": _backtest_record(result, best),
    }


def _metric(row: pd.Series, column: str):
    value = row.get(column)
    return None if value is None or pd.isna(value) else float(value)


def _backtest_record(result: BacktestResult, best: str | None) -> dict:
    """JSON-ready summary of a backtest for the user's history (InsForge)."""
    config = result.config
    strategies = []
    for label, row in result.metrics.iterrows():
        dates = result.rebalance_dates.get(label, [])
        rebalances = row.get("Rebalanceos")
        strategies.append(
            {
                "strategy": str(label),
                "total_return": _metric(row, "Retorno total"),
                "cagr": _metric(row, "CAGR"),
                "volatility": _metric(row, "Volatilidad anualizada"),
                "sharpe": _metric(row, "Ratio de Sharpe"),
                "sortino": _metric(row, "Ratio de Sortino"),
                "max_drawdown": _metric(row, "Máxima caída"),
                "rebalances": None if rebalances is None or pd.isna(rebalances) else int(rebalances),
                "total_costs": _metric(row, "Costos totales"),
                "rebalance_dates": [day.date().isoformat() for day in dates],
            }
        )
    return {
        "tickers": list(result.assets),
        "start_date": result.start.date().isoformat(),
        "end_date": result.end.date().isoformat(),
        "trading_days": len(result.values),
        "config": {
            "lookback": config.lookback,
            "drift_threshold": config.drift_threshold,
            "vol_window": config.vol_window,
            "vol_threshold": config.vol_threshold,
            "fees": config.fees,
            "slippage": config.slippage,
            "init_cash": config.init_cash,
        },
        "best_strategy": best,
        "strategies": strategies,
    }


def run_backtest_section(
    assets: list[str],
    start_date: date,
    bounds: WeightBounds,
    config: BacktestConfig,
) -> dict:
    """Backtest view model, or ``{"error": message}`` without failing the page."""
    started = time.perf_counter()
    try:
        prices = _backtest_prices(assets, start_date, config)
        result, from_cache = cached_backtest(
            prices, config, bounds.restricted_to(list(prices.columns)), start_date
        )
    except (requests.RequestException, TimeoutError, YFException) as error:
        logger.exception("Price download failed during the HRP backtest")
        return {"error": f"No se pudieron descargar los datos del backtest: {error}"}
    except ValueError as error:
        return {"error": str(error)}
    view = backtest_view(result, from_cache, time.perf_counter() - started)
    view["record"]["bounds"] = _bounds_record(bounds)
    return view


def _bounds_record(bounds: WeightBounds) -> dict:
    return {
        "min_weight": bounds.min_weight,
        "max_weight": bounds.max_weight,
        "asset_min_weights": dict(sorted(bounds.asset_min_weights.items())),
        "asset_max_weights": dict(sorted(bounds.asset_max_weights.items())),
    }


def run_hrp(
    tickers: list[str],
    benchmark: str,
    start_date: date,
    bounds: WeightBounds | None = None,
    labels: dict[str, str] | None = None,
    backtest: BacktestConfig | None = None,
) -> dict:
    """Optimize the portfolio and return everything the HRP page renders.

    ``labels`` maps tickers to catalog labels (e.g. "AAPL" -> "Apple (AAPL)").
    ``charts`` keys: ``allocation``, the historical charts (``cumulative``,
    ``drawdown``, ``monthly_heatmap``, ``rolling_sharpe``,
    ``returns_distribution``, ``yearly_returns``), ``correlation``,
    ``dendrogram``, ``risk_contribution``, ``return_contribution``,
    ``monthly_volatility``, ``returns_box`` and ``returns_matrix`` (portfolio,
    benchmark and assets); keys without data are omitted.
    With ``backtest`` the result's ``backtest`` key holds ``backtest_view()``
    (or ``{"error": ...}``): a walk-forward vectorbt backtest of the HRP
    portfolio over the same period with several rebalancing triggers.
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
        "returns_matrix": _returns_matrix(run, benchmark, chart_names),
    }
    portfolio_metrics = calculate_quantstats_metrics(run.portfolio_returns)["Valor"]
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
                portfolio_metrics.to_frame("Valor").rename(
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
        "summary": {
            "weights": {ticker: float(weight) for ticker, weight in weights.items()},
            "bounds": _bounds_record(bounds.restricted_to(assets)),
            "metrics": {
                "expected_return": portfolio_metrics.get("Retorno anual compuesto"),
                "volatility": portfolio_metrics.get("Volatilidad anualizada"),
                "sharpe": portfolio_metrics.get("Ratio de Sharpe"),
                "sortino": portfolio_metrics.get(_QUANTSTATS_SORTINO),
                "max_drawdown": portfolio_metrics.get("Máxima caída"),
            },
        },
        "backtest": (
            run_backtest_section(assets, start_date, bounds, backtest)
            if backtest is not None
            else None
        ),
    }
