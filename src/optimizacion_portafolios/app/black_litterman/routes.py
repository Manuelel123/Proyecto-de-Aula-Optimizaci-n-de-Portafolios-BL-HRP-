"""Black-Litterman routes and portfolio workflow."""

import logging
from datetime import date
from io import BytesIO

import matplotlib
import numpy as np
import pandas as pd
import requests
from flask import flash, render_template, request, send_file
from yfinance.exceptions import YFException, YFRateLimitError

matplotlib.use("Agg")

from optimizacion_portafolios.analytics import (
    calculate_quantstats_metrics,
    date_one_year_ago,
    generate_tearsheet,
)
from optimizacion_portafolios.black_litterman import optimizar_black_litterman
from optimizacion_portafolios.catalogs import (
    ACTIVOS_HRP,
    BENCHMARKS,
    UNIVERSOS_BLACK_LITTERMAN,
)
from optimizacion_portafolios.market_data import (
    download_prices,
    fetch_market_cap_usd,
)
from optimizacion_portafolios.app.black_litterman import bp
from optimizacion_portafolios.app.common.forms import selected_option, selected_tickers
from optimizacion_portafolios.app.common.portfolio_views import (
    bar_chart,
    dataframe_html,
    metrics_html,
    quantstats_charts,
)

logger = logging.getLogger(__name__)
_OBJECTIVES = {
    "Máximo Sharpe": "max_sharpe",
    "Mínima volatilidad": "min_volatility",
    "Pesos implícitos del modelo": "model_weights",
}
_ROLLING_SHARPE_PERIOD = 126


def _format_percent(value) -> str:
    return "N/D" if pd.isna(value) else f"{value:.2%}"


def _run_black_litterman(
    tickers: list[str],
    views: dict[str, float],
    objective: str,
    risk_free_rate: float,
    benchmark: str,
    start_date: date,
    names: dict[str, str],
    download_report: bool,
):
    today = date.today()
    downloaded_prices = download_prices(tuple(tickers), start_date, today)
    prices = downloaded_prices.dropna(axis="columns", how="all").ffill().dropna()
    missing_prices = sorted(set(tickers) - set(prices.columns))
    if missing_prices:
        tickers = [ticker for ticker in tickers if ticker in prices.columns]
    if prices.shape[1] < 2:
        raise ValueError("Yahoo Finance devolvió datos para menos de dos activos.")

    benchmark_prices = download_prices((benchmark,), start_date, today)
    if benchmark not in benchmark_prices:
        raise ValueError(f"No se encontraron precios para el benchmark {benchmark}.")
    market_prices = benchmark_prices[benchmark]

    capitalizations = {}
    missing_capitalizations = []
    for ticker in prices.columns:
        amount = fetch_market_cap_usd(ticker)
        if amount is None:
            missing_capitalizations.append(ticker)
        else:
            capitalizations[ticker] = amount
    if missing_capitalizations:
        raise ValueError(
            "Yahoo Finance no devolvió una capitalización bursátil positiva para: "
            + ", ".join(missing_capitalizations)
        )

    active_views = {ticker: views[ticker] for ticker in prices.columns}
    confidences = {ticker: 0.95 for ticker in prices.columns}
    objective_code = _OBJECTIVES[objective]
    model = optimizar_black_litterman(
        precios=prices,
        vistas_absolutas=active_views,
        confianzas=confidences,
        objetivo=objective_code,
        tasa_libre_riesgo=risk_free_rate,
        capitalizaciones=capitalizations,
        precios_mercado=market_prices,
    )

    results_table = pd.DataFrame(
        {
            "Activo": [names.get(ticker, ticker) for ticker in model.pesos.index],
            "Ticker": model.pesos.index,
            "Peso": model.pesos.values,
            "Capitalización / activos netos (USD)": [
                capitalizations[ticker] for ticker in model.pesos.index
            ],
            "View anual": [active_views[ticker] for ticker in model.pesos.index],
            "Confianza": [confidences[ticker] for ticker in model.pesos.index],
            "Retorno prior": model.retornos_prior.reindex(model.pesos.index).values,
            "Retorno posterior": model.retornos_posteriores.reindex(
                model.pesos.index
            ).values,
        }
    )
    results_html = dataframe_html(
        results_table,
        {
            "Peso": _format_percent,
            "Capitalización / activos netos (USD)": lambda value: f"{value:,.0f}",
            "View anual": _format_percent,
            "Confianza": _format_percent,
            "Retorno prior": _format_percent,
            "Retorno posterior": _format_percent,
        },
        index=False,
    )
    covariance_html = dataframe_html(
        model.covarianza_posterior,
        {
            column: lambda value: f"{value:.6f}"
            for column in model.covarianza_posterior
        },
    )
    weights_chart = bar_chart(
        model.pesos.rename(index=lambda ticker: names.get(ticker, ticker)),
        "Asignación Black-Litterman",
        "Peso del portafolio",
    )

    returns = prices.pct_change(fill_method=None).dropna(how="all")
    portfolio_returns = returns.loc[:, model.pesos.index].dot(model.pesos).rename(
        "Portafolio Black Litterman"
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
            aligned["Portafolio Black Litterman"],
            aligned[benchmark],
            "Portafolio Black Litterman",
            "reporte_quantstats_black_litterman.html",
        )
        return {
            "report": report,
            "missing_prices": missing_prices,
        }
    history = None
    if len(aligned) >= 2:
        portfolio_returns = aligned["Portafolio Black Litterman"]
        benchmark_returns = aligned[benchmark]
        metrics = calculate_quantstats_metrics(portfolio_returns)
        metrics_table = metrics_html(metrics)
        charts, has_rolling_sharpe = quantstats_charts(
            portfolio_returns,
            benchmark_returns,
            rolling_period=_ROLLING_SHARPE_PERIOD,
        )
        history = {
            "metrics": metrics_table,
            "charts": charts,
            "observations": len(aligned),
            "has_rolling_sharpe": has_rolling_sharpe,
        }
    return {
        "expected_return": model.retorno_esperado,
        "volatility": model.volatilidad,
        "sharpe": model.sharpe,
        "weights_sum": float(model.pesos.sum()),
        "weights_chart": weights_chart,
        "results_table": results_html,
        "covariance_table": covariance_html,
        "history": history,
        "missing_prices": missing_prices,
        "included_tickers": list(prices.columns),
        "excluded_tickers": missing_prices,
    }


@bp.route("/black-litterman", methods=["GET", "POST"])
def black_litterman():
    today = date.today()
    default_start = date_one_year_ago(date_one_year_ago(today))
    universe_names = list(UNIVERSOS_BLACK_LITTERMAN)
    universe_name = request.values.get("universe", "Portafolio actual")
    if universe_name not in UNIVERSOS_BLACK_LITTERMAN:
        flash("Selecciona un universo de activos válido.", "error")
        universe_name = "Portafolio actual"
    universe = UNIVERSOS_BLACK_LITTERMAN[universe_name]
    available_tickers = list(universe.values())
    selected = request.form.getlist("tickers") if request.method == "POST" else []
    default_selection = (
        available_tickers if universe_name == "Portafolio actual" else available_tickers[:4]
    )
    if request.method == "POST" and request.form.get("action") == "refresh":
        selected = [ticker for ticker in selected if ticker in available_tickers]
        if not selected:
            selected = default_selection
    elif not selected:
        selected = default_selection

    benchmark_names = list(BENCHMARKS)
    benchmark_name = request.values.get("benchmark", benchmark_names[0])
    if benchmark_name not in BENCHMARKS:
        benchmark_name = benchmark_names[0]
    objective_names = list(_OBJECTIVES)
    objective_name = request.values.get("objective", objective_names[0])
    if objective_name not in _OBJECTIVES:
        objective_name = objective_names[0]
    risk_free_value = request.values.get("risk_free_rate", "2.0")
    start_value = request.values.get("start_date", default_start.isoformat())
    views_values = {}
    for ticker in selected:
        value = request.values.get(f"view_{ticker}", "8.0")
        views_values[ticker] = value
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            selected = selected_tickers()
            custom = {
                ticker.strip().upper()
                for ticker in request.form.get("custom_tickers", "").split(",")
                if ticker.strip()
            }
            allowed = set(ACTIVOS_HRP.values()) | custom
            invalid = [ticker for ticker in selected if ticker not in allowed]
            if invalid:
                raise ValueError("Activos no disponibles: " + ", ".join(invalid))
            if len(selected) < 2:
                raise ValueError(
                    "Selecciona al menos dos activos para calcular el portafolio."
                )
            objective_name = selected_option(
                "objective", _OBJECTIVES, objective_names[0]
            )
            benchmark_name = selected_option(
                "benchmark", BENCHMARKS, benchmark_names[0]
            )
            try:
                risk_free_rate_pct = float(request.form.get("risk_free_rate", "2.0"))
            except ValueError as error:
                raise ValueError("La tasa libre de riesgo debe ser numérica.") from error
            if not np.isfinite(risk_free_rate_pct) or not 0 <= risk_free_rate_pct <= 100:
                raise ValueError(
                    "La tasa libre de riesgo debe estar entre 0% y 100%."
                )
            risk_free_value = str(risk_free_rate_pct)
            views = {}
            for ticker in selected:
                try:
                    view_pct = float(request.form.get(f"view_{ticker}", "8.0"))
                except ValueError as error:
                    raise ValueError(
                        f"La view anual de {ticker} debe ser numérica."
                    ) from error
                if not np.isfinite(view_pct) or not -100 <= view_pct <= 1000:
                    raise ValueError(
                        f"La view anual de {ticker} debe estar entre -100% y 1000%."
                    )
                views[ticker] = view_pct / 100
                views_values[ticker] = str(view_pct)
            start_date = date.fromisoformat(
                request.form.get("start_date", default_start.isoformat())
            )
            if start_date > today:
                raise ValueError("La fecha inicial no puede ser posterior a hoy.")
            names = {ticker: label for label, ticker in universe.items()}
            names.update({ticker: ticker for ticker in custom})
            try:
                result = _run_black_litterman(
                    selected,
                    views,
                    objective_name,
                    risk_free_rate_pct / 100,
                    BENCHMARKS[benchmark_name],
                    start_date,
                    names,
                    request.form.get("action") == "download_report",
                )
            except (requests.RequestException, TimeoutError, YFException) as error:
                logger.exception(
                    "Yahoo Finance request failed during Black-Litterman optimization"
                )
                if isinstance(error, YFRateLimitError):
                    message = (
                        "Yahoo Finance limitó temporalmente las consultas. "
                        "Espera unos minutos y vuelve a intentar."
                    )
                else:
                    message = f"No se pudieron descargar los datos: {error}"
                raise ValueError(message) from error
            if result.get("report"):
                return send_file(
                    BytesIO(result["report"]),
                    mimetype="text/html",
                    as_attachment=True,
                    download_name="reporte_quantstats_black_litterman.html",
                )
            if result["missing_prices"]:
                flash(
                    "Se excluyeron activos sin precios disponibles: "
                    + ", ".join(result["missing_prices"]),
                    "warning",
                )
            history = result["history"]
            if history and not history["has_rolling_sharpe"]:
                flash(
                    "El Sharpe móvil requiere al menos 126 retornos alineados; "
                    "los demás resultados están disponibles.",
                    "warning",
                )
            if history is None:
                flash(
                    "No hay suficientes retornos comunes con el benchmark para generar "
                    "el análisis QuantStats.",
                    "warning",
                )
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    return render_template(
        "black_litterman/black_litterman.html",
        today=today.isoformat(),
        default_start=start_value,
        universe_names=universe_names,
        universe_name=universe_name,
        available_tickers=available_tickers,
        selected_tickers=selected,
        benchmark_names=benchmark_names,
        benchmark_name=benchmark_name,
        objective_names=objective_names,
        objective_name=objective_name,
        risk_free_value=risk_free_value,
        views_values=views_values,
        result=result,
    )
