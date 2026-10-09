"""HRP workflow: download data, optimize and build the page view model."""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from optimizacion_portafolios.analytics.performance import (
    calculate_quantstats_metrics,
    generate_tearsheet,
)
from optimizacion_portafolios.analytics.volatility import calculate_monthly_volatility
from optimizacion_portafolios.data.market_data import download_prices
from optimizacion_portafolios.models.hrp import (
    calculate_hrp,
    calculate_hrp_contributions,
)
from optimizacion_portafolios.web.common.charts import (
    bar_chart,
    correlation_chart,
    line_chart,
    monthly_volatility_histograms,
    quantstats_charts,
)
from optimizacion_portafolios.web.common.tables import (
    dataframe_html,
    format_percent,
    metrics_html,
)

ROLLING_SHARPE_PERIOD = 126
REPORT_FILENAME = "reporte_quantstats_hrp.html"
_PORTFOLIO_COLUMN = "Portafolio HRP"


@dataclass(frozen=True)
class HrpRun:
    prices: pd.DataFrame
    returns: pd.DataFrame
    weights: pd.Series
    ordered_correlation: pd.DataFrame
    portfolio_returns: pd.Series
    benchmark_returns: pd.Series
    missing: list[str]


def _optimize(tickers: list[str], benchmark: str, start_date: date) -> HrpRun:
    today = date.today()
    prices = download_prices(tuple(tickers), start_date, today)
    prices = prices.dropna(axis="columns", how="all").ffill().dropna()
    if prices.shape[1] < 2:
        raise ValueError(
            "Yahoo Finance devolvió datos para menos de dos activos seleccionados."
        )
    missing = sorted(set(tickers) - set(prices.columns))
    returns = prices.pct_change(fill_method=None).dropna(how="all").dropna()
    if len(returns) < 2:
        raise ValueError("Se necesitan más observaciones para optimizar HRP.")

    weights, ordered_correlation = calculate_hrp(returns)
    benchmark_prices = download_prices((benchmark,), start_date, today)
    if benchmark_prices.empty or benchmark not in benchmark_prices:
        raise ValueError(
            f"Yahoo Finance no devolvió datos para el benchmark {benchmark}."
        )
    benchmark_returns = benchmark_prices[benchmark].pct_change(
        fill_method=None
    ).rename(benchmark)
    portfolio_returns = returns.dot(weights).rename(_PORTFOLIO_COLUMN)
    aligned = pd.concat(
        [portfolio_returns, benchmark_returns], axis=1, sort=False
    ).dropna()
    if aligned.empty:
        raise ValueError(
            f"No hay retornos coincidentes entre el portafolio y el benchmark {benchmark}."
        )
    return HrpRun(
        prices=prices,
        returns=returns,
        weights=weights,
        ordered_correlation=ordered_correlation,
        portfolio_returns=aligned[_PORTFOLIO_COLUMN],
        benchmark_returns=aligned[benchmark],
        missing=missing,
    )


def build_hrp_report(
    tickers: list[str], benchmark: str, start_date: date
) -> tuple[bytes, list[str]]:
    """Return the QuantStats HTML tearsheet and the tickers without data."""
    run = _optimize(tickers, benchmark, start_date)
    report = generate_tearsheet(
        run.portfolio_returns,
        run.benchmark_returns,
        _PORTFOLIO_COLUMN,
        REPORT_FILENAME,
    )
    return report, run.missing


def run_hrp(tickers: list[str], benchmark: str, start_date: date) -> dict:
    """Optimize the portfolio and return everything the HRP page renders."""
    run = _optimize(tickers, benchmark, start_date)
    prices, returns, weights = run.prices, run.returns, run.weights

    weights_frame = pd.DataFrame(
        {"Activo": weights.index, "Ticker": weights.index, "Peso": weights.values}
    )
    _, contributions = calculate_hrp_contributions(returns, weights)
    contributions = contributions.rename_axis("Activo")
    charts, has_rolling_sharpe = quantstats_charts(
        run.portfolio_returns,
        run.benchmark_returns,
        rolling_period=ROLLING_SHARPE_PERIOD,
    )
    monthly_volatility = calculate_monthly_volatility(returns)
    charts.update(
        {
            "Evolución de precios": line_chart(
                prices, "Precios ajustados", "Precio de cierre"
            ),
            "Retornos por activo": line_chart(
                returns, "Retornos históricos por activo", "Retorno diario"
            ),
            "Retorno del portafolio": line_chart(
                run.portfolio_returns,
                "Retornos diarios del portafolio HRP",
                "Retorno diario",
            ),
            "Evolución unitaria": line_chart(
                prices.div(prices.iloc[0]), "Rendimiento relativo · inicio = 1", "Índice"
            ),
            "Correlación": correlation_chart(run.ordered_correlation),
            "Pesos": bar_chart(weights, "Asignación por activo", "Peso"),
            "Volatilidad mensual": (
                monthly_volatility_histograms(monthly_volatility)
                if not monthly_volatility.empty
                else None
            ),
        }
    )
    return {
        "charts": charts,
        "tables": {
            "weights": dataframe_html(
                weights_frame, {"Peso": format_percent}, index=False
            ),
            "contributions": dataframe_html(
                contributions,
                {column: format_percent for column in contributions.columns},
            ),
            "metrics": metrics_html(
                calculate_quantstats_metrics(run.portfolio_returns)
            ),
            "correlation": dataframe_html(
                run.ordered_correlation,
                {
                    column: lambda value: f"{value:.2f}"
                    for column in run.ordered_correlation
                },
            ),
        },
        "start": prices.index.min().date().isoformat(),
        "end": prices.index.max().date().isoformat(),
        "observations": len(returns),
        "common_observations": len(run.portfolio_returns),
        "weight_sum": float(weights.sum()),
        "missing": run.missing,
        "has_rolling_sharpe": has_rolling_sharpe,
        "selected_tickers": list(prices.columns),
        "benchmark": benchmark,
    }
