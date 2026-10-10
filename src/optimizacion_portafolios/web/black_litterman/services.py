"""Black-Litterman workflow: data, market caps, model and page view model."""

import csv
from datetime import date
from io import StringIO

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
from optimizacion_portafolios.web.common.charts import (
    allocation_chart,
    correlation_heatmap,
    historical_charts,
    prior_posterior_chart,
)
from optimizacion_portafolios.web.common.price_series import price_series_payload
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
# <optgroup> families of the objective selector (values are OBJECTIVES keys).
OBJECTIVE_GROUPS = (
    (
        "Rentabilidad ajustada por riesgo",
        ("Máximo ratio de Sharpe", "Máximo ratio de Sortino"),
    ),
    ("Minimizar riesgo", ("Mínima varianza", "Mínimo CVaR (95%)")),
    (
        "Rentabilidad / utilidad",
        ("Máxima rentabilidad esperada", "Máxima utilidad (δ del mercado)"),
    ),
    ("Avanzado", (UNCONSTRAINED_OBJECTIVE,)),
)
OBJECTIVE_HELP = {
    "Máximo ratio de Sharpe": "Maximiza el exceso de retorno por unidad de volatilidad.",
    "Máximo ratio de Sortino": (
        "Maximiza el exceso de retorno por unidad de semidesviación "
        "(solo penaliza la volatilidad a la baja)."
    ),
    "Mínima varianza": "Minimiza la varianza del portafolio sin mirar el retorno esperado.",
    "Mínimo CVaR (95%)": "Minimiza la pérdida media del peor 5 % de los días.",
    "Máxima rentabilidad esperada": (
        "Maximiza el retorno posterior; suele concentrarse en el activo más prometedor."
    ),
    "Máxima utilidad (δ del mercado)": (
        "Maximiza w'μ − δ/2·w'Σw con la aversión al riesgo implícita del mercado."
    ),
    UNCONSTRAINED_OBJECTIVE: (
        "Maximiza w'μ − δ/2·w'Σw sin restricción long-only: admite posiciones "
        "cortas y apalancadas."
    ),
}
VIEW_CONFIDENCE = 0.95
DEFAULT_VIEW = "8.0"
ROLLING_SHARPE_PERIOD = 126
WINDOW_YEARS = 2
CURRENT_PORTFOLIO = "Portafolio actual"
REPORT_FILENAME = "reporte_quantstats_black_litterman.html"
CSV_FILENAME = "asignacion_black_litterman.csv"
LOADING_STEPS = (
    "Descargando precios",
    "Capitalizaciones de mercado",
    "Prior de equilibrio",
    "Aplicando views",
    "Optimizando",
)
_PORTFOLIO_COLUMN = "Portafolio Black Litterman"
_HISTORICAL_LABELS = {
    "Ratio de Sharpe": "Ratio de Sharpe histórico",
    "Ratio de Sortino": "Ratio de Sortino histórico",
}


def default_selection(universe_name: str, tickers) -> list[str]:
    """Checked tickers by default: all of "Portafolio actual", else the first 4."""
    tickers = list(tickers)
    return tickers if universe_name == CURRENT_PORTFOLIO else tickers[:4]


def display_date(value) -> str:
    return value.strftime("%d/%m/%Y")


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


def _signed_points(value: float) -> str:
    """Percentage-point difference with an explicit sign (e.g. ``+3.25``)."""
    points = value * 100
    return "0.00" if abs(points) < 0.005 else f"{points:+.2f}"


def _allocation_rows(model, names, views) -> list[dict]:
    """Rows of the allocation table, sorted by Black-Litterman weight (desc)."""
    rows = []
    for ticker in model.weights.sort_values(ascending=False).index:
        weight = float(model.weights[ticker])
        market_weight = float(model.market_weights[ticker])
        posterior = float(model.posterior_returns[ticker])
        rows.append(
            {
                "ticker": ticker,
                "name": names.get(ticker, ticker),
                "weight": weight,
                "weight_text": format_percent(weight),
                "market_weight": market_weight,
                "market_weight_text": format_percent(market_weight),
                "tilt": weight - market_weight,
                "tilt_text": _signed_points(weight - market_weight),
                "view": float(views[ticker]),
                "view_text": format_percent(views[ticker]),
                "posterior": posterior,
                "posterior_text": format_percent(posterior),
            }
        )
    return rows


def _prior_rows(model, names, views) -> list[dict]:
    rows = []
    for ticker in model.weights.index:
        prior = float(model.prior_returns[ticker])
        posterior = float(model.posterior_returns[ticker])
        rows.append(
            {
                "ticker": ticker,
                "name": names.get(ticker, ticker),
                "prior_text": format_percent(prior),
                "view_text": format_percent(views[ticker]),
                "confidence_text": f"{model.view_confidences[ticker]:.0%}",
                "posterior_text": format_percent(posterior),
                "shift": posterior - prior,
                "shift_text": _signed_points(posterior - prior),
            }
        )
    return rows


def _assumption_rows(model, names, capitalizations) -> list[dict]:
    return [
        {
            "ticker": ticker,
            "name": names.get(ticker, ticker),
            "market_cap_text": f"{capitalizations[ticker]:,.0f}",
            "market_weight_text": format_percent(model.market_weights[ticker]),
            "confidence_text": f"{model.view_confidences[ticker]:.0%}",
        }
        for ticker in model.weights.index
    ]


def _allocation_csv(rows: list[dict]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "Ticker",
            "Activo",
            "Peso BL",
            "Peso de mercado",
            "Inclinacion (pp)",
            "View anual",
            "Retorno posterior",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["ticker"],
                row["name"],
                f"{row['weight']:.6f}",
                f"{row['market_weight']:.6f}",
                f"{row['tilt'] * 100:.4f}",
                f"{row['view']:.6f}",
                f"{row['posterior']:.6f}",
            ]
        )
    return buffer.getvalue()


def run_black_litterman(
    tickers: list[str],
    views: dict[str, float],
    objective: str,
    risk_free_rate: float,
    benchmark: str,
    start_date: date,
    names: dict[str, str],
    download_report: bool,
    benchmark_label: str | None = None,
) -> dict:
    """Run the model; return the page view model or, if requested, the report."""
    today = date.today()
    raw_prices = download_prices(tuple(tickers), start_date, today)
    # Keep the unaligned prices for the price explorer (no ffill across assets).
    raw_prices = raw_prices.dropna(axis="columns", how="all")
    prices = raw_prices.ffill().dropna()
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

    tickers_in_model = list(model.weights.index)
    labels = {ticker: names.get(ticker, ticker) for ticker in tickers_in_model}
    allocation_rows = _allocation_rows(model, labels, active_views)
    posterior_covariance = model.posterior_covariance

    history = None
    if len(aligned) >= 2:
        history_charts, has_rolling_sharpe = historical_charts(
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
            "charts": history_charts,
            "observations": len(aligned),
            "start": display_date(aligned.index[0]),
            "end": display_date(aligned.index[-1]),
            "has_rolling_sharpe": has_rolling_sharpe,
        }

    return {
        "expected_return": model.expected_return,
        "volatility": model.volatility,
        "sharpe": model.sharpe,
        "sortino": model.sortino,
        "risk_aversion": model.risk_aversion,
        "tau": model.tau,
        "view_confidence": VIEW_CONFIDENCE,
        "risk_free_rate": risk_free_rate,
        "trading_days": TRADING_DAYS,
        "weights_sum": float(model.weights.sum()),
        "market_weights_sum": float(model.market_weights.sum()),
        "has_negative_weights": bool((model.weights < -1e-9).any()),
        "n_assets": len(tickers_in_model),
        "window": {
            "years": WINDOW_YEARS,
            "start": display_date(start_date),
            "end": display_date(today),
            "effective_start": display_date(prices.index[0]),
            "effective_end": display_date(prices.index[-1]),
            "observations": len(returns),
        },
        "allocation_rows": allocation_rows,
        "allocation_csv": _allocation_csv(allocation_rows),
        "prior_rows": _prior_rows(model, labels, active_views),
        "assumption_rows": _assumption_rows(model, labels, capitalizations),
        # Equilibrium returns in percentage points, pre-filled in the views form.
        "prior_returns": {
            ticker: round(float(model.prior_returns[ticker]) * 100, 2)
            for ticker in tickers_in_model
        },
        "charts": {
            "allocation": allocation_chart(
                model.weights, labels, market_weights=model.market_weights
            ),
            "prior_posterior": prior_posterior_chart(
                model.prior_returns,
                pd.Series(active_views).reindex(tickers_in_model),
                model.posterior_returns,
            ),
            "posterior_correlation": correlation_heatmap(
                model.posterior_correlation, "Correlación posterior"
            ),
        },
        "prices_payload": price_series_payload(
            raw_prices.loc[:, tickers_in_model],
            {**labels, benchmark: benchmark_label or benchmark},
            market_prices.dropna().rename(benchmark),
        ),
        "covariance_table": dataframe_html(
            posterior_covariance,
            {column: lambda value: f"{value:.6f}" for column in posterior_covariance},
        ),
        "history": history,
        "missing_prices": missing_prices,
        "included_tickers": list(prices.columns),
        "excluded_tickers": missing_prices,
    }
