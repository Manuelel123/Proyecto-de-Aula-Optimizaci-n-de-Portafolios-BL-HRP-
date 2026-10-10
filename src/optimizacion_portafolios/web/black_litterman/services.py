"""Black-Litterman workflow: data, market caps, model and page view model."""

from datetime import date

import pandas as pd

from optimizacion_portafolios.analytics.performance import (
    calculate_quantstats_metrics,
    generate_tearsheet,
)
from optimizacion_portafolios.data.market_data import (
    download_prices,
    fetch_market_cap_usd,
)
from optimizacion_portafolios.models.black_litterman import (
    TRADING_DAYS,
    optimize_black_litterman,
)
from optimizacion_portafolios.web.common.legacy_charts import bar_chart, quantstats_charts
from optimizacion_portafolios.web.common.tables import (
    dataframe_html,
    format_percent,
    metrics_html,
)

OBJECTIVES = {
    "Máximo ratio de Sharpe": "max_sharpe",
    "Máximo ratio de Sortino": "max_sortino",
    "Mínima varianza": "min_variance",
    "Mínimo CVaR (95%)": "min_cvar",
    "Máxima rentabilidad esperada": "max_return",
    "Máxima utilidad (δ del mercado)": "max_utility",
    "Máxima utilidad sin restricción long-only": "max_utility_unconstrained",
}
UNCONSTRAINED_OBJECTIVE = "Máxima utilidad sin restricción long-only"
VIEW_CONFIDENCE = 0.95
ROLLING_SHARPE_PERIOD = 126
REPORT_FILENAME = "reporte_quantstats_black_litterman.html"
_PORTFOLIO_COLUMN = "Portafolio Black Litterman"
_HISTORICAL_LABELS = {
    "Ratio de Sharpe": "Ratio de Sharpe histórico",
    "Ratio de Sortino": "Ratio de Sortino histórico",
}


def _market_caps_usd(tickers) -> dict[str, float]:
    capitalizations = {}
    missing = []
    for ticker in tickers:
        amount = fetch_market_cap_usd(ticker)
        if amount is None:
            missing.append(ticker)
        else:
            capitalizations[ticker] = amount
    if missing:
        raise ValueError(
            "Yahoo Finance no devolvió una capitalización bursátil positiva para: "
            + ", ".join(missing)
        )
    return capitalizations


def run_black_litterman(
    tickers: list[str],
    views: dict[str, float],
    objective: str,
    risk_free_rate: float,
    benchmark: str,
    start_date: date,
    names: dict[str, str],
    download_report: bool,
) -> dict:
    """Run the model; return the page view model or, if requested, the report."""
    today = date.today()
    prices = download_prices(tuple(tickers), start_date, today)
    prices = prices.dropna(axis="columns", how="all").ffill().dropna()
    missing_prices = sorted(set(tickers) - set(prices.columns))
    if prices.shape[1] < 2:
        raise ValueError("Yahoo Finance devolvió datos para menos de dos activos.")

    benchmark_prices = download_prices((benchmark,), start_date, today)
    if benchmark not in benchmark_prices:
        raise ValueError(f"No se encontraron precios para el benchmark {benchmark}.")
    market_prices = benchmark_prices[benchmark]

    capitalizations = _market_caps_usd(prices.columns)
    active_views = {ticker: views[ticker] for ticker in prices.columns}
    confidences = {ticker: VIEW_CONFIDENCE for ticker in prices.columns}
    model = optimize_black_litterman(
        prices=prices,
        views=active_views,
        confidences=confidences,
        objective=OBJECTIVES[objective],
        risk_free_rate=risk_free_rate,
        market_caps=capitalizations,
        market_prices=market_prices,
    )

    returns = prices.pct_change(fill_method=None).dropna(how="all")
    portfolio_returns = returns.loc[:, model.weights.index].dot(model.weights).rename(
        _PORTFOLIO_COLUMN
    )
    benchmark_returns = market_prices.pct_change(fill_method=None).rename(benchmark)
    aligned = pd.concat(
        [portfolio_returns, benchmark_returns], axis=1, sort=False
    ).dropna()
    if download_report:
        if len(aligned) < 2:
            raise ValueError(
                "No hay suficientes retornos comunes para generar el informe."
            )
        report = generate_tearsheet(
            aligned[_PORTFOLIO_COLUMN],
            aligned[benchmark],
            _PORTFOLIO_COLUMN,
            REPORT_FILENAME,
        )
        return {"report": report, "missing_prices": missing_prices}

    weights_index = model.weights.index
    results_table = pd.DataFrame(
        {
            "Activo": [names.get(ticker, ticker) for ticker in weights_index],
            "Ticker": weights_index,
            "Peso": model.weights.values,
            "Capitalización / activos netos (USD)": [
                capitalizations[ticker] for ticker in weights_index
            ],
            "Peso de mercado": model.market_weights.reindex(weights_index).values,
            "View anual": [active_views[ticker] for ticker in weights_index],
            "Confianza": model.view_confidences.reindex(weights_index).values,
            "Retorno prior (equilibrio)": model.prior_returns.reindex(
                weights_index
            ).values,
            "Retorno posterior": model.posterior_returns.reindex(
                weights_index
            ).values,
        }
    )
    history = None
    if len(aligned) >= 2:
        charts, has_rolling_sharpe = quantstats_charts(
            aligned[_PORTFOLIO_COLUMN],
            aligned[benchmark],
            rolling_period=ROLLING_SHARPE_PERIOD,
        )
        history = {
            "metrics": metrics_html(
                calculate_quantstats_metrics(aligned[_PORTFOLIO_COLUMN]).rename(
                    index=_HISTORICAL_LABELS
                )
            ),
            "charts": charts,
            "observations": len(aligned),
            "has_rolling_sharpe": has_rolling_sharpe,
        }
    return {
        "expected_return": model.expected_return,
        "volatility": model.volatility,
        "sharpe": model.sharpe,
        "sortino": model.sortino,
        "risk_aversion": model.risk_aversion,
        "tau": model.tau,
        "trading_days": TRADING_DAYS,
        "weights_sum": float(model.weights.sum()),
        "weights_chart": bar_chart(
            model.weights.rename(index=lambda ticker: names.get(ticker, ticker)),
            "Asignación Black-Litterman",
            "Peso del portafolio",
        ),
        "results_table": dataframe_html(
            results_table,
            {
                "Peso": format_percent,
                "Capitalización / activos netos (USD)": lambda value: f"{value:,.0f}",
                "Peso de mercado": format_percent,
                "View anual": format_percent,
                "Confianza": format_percent,
                "Retorno prior (equilibrio)": format_percent,
                "Retorno posterior": format_percent,
            },
            index=False,
        ),
        "covariance_table": dataframe_html(
            model.posterior_covariance,
            {
                column: lambda value: f"{value:.6f}"
                for column in model.posterior_covariance
            },
        ),
        "history": history,
        "missing_prices": missing_prices,
        "included_tickers": list(prices.columns),
        "excluded_tickers": missing_prices,
    }
