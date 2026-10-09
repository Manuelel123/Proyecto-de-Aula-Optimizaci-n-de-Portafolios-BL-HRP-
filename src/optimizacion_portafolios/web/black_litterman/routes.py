"""Black-Litterman HTTP routes."""

import logging
from datetime import date
from io import BytesIO

import numpy as np
import requests
from flask import flash, render_template, request, send_file
from yfinance.exceptions import YFException, YFRateLimitError

from optimizacion_portafolios.analytics.statistics import date_years_ago
from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_HRP,
    BENCHMARKS,
    UNIVERSOS_BLACK_LITTERMAN,
)
from optimizacion_portafolios.web.black_litterman import bp
from optimizacion_portafolios.web.black_litterman.services import (
    OBJECTIVES,
    REPORT_FILENAME,
    ROLLING_SHARPE_PERIOD,
    run_black_litterman,
)
from optimizacion_portafolios.web.common.forms import (
    custom_tickers,
    parse_bounded_float,
    parse_start_date,
    selected_option,
    selected_tickers,
)

logger = logging.getLogger(__name__)
_DEFAULT_UNIVERSE = "Portafolio actual"
_DEFAULT_VIEW = "8.0"
_WINDOW_YEARS = 2


@bp.route("/black-litterman", methods=["GET", "POST"])
def black_litterman():
    today = date.today()
    default_start = date_years_ago(today, _WINDOW_YEARS)
    universe_names = list(UNIVERSOS_BLACK_LITTERMAN)
    universe_name = request.values.get("universe", _DEFAULT_UNIVERSE)
    if universe_name not in UNIVERSOS_BLACK_LITTERMAN:
        flash("Selecciona un universo de activos válido.", "error")
        universe_name = _DEFAULT_UNIVERSE
    universe = UNIVERSOS_BLACK_LITTERMAN[universe_name]
    available_tickers = list(universe.values())
    selected = request.form.getlist("tickers") if request.method == "POST" else []
    default_selection = (
        available_tickers
        if universe_name == _DEFAULT_UNIVERSE
        else available_tickers[:4]
    )
    if request.method == "POST" and request.form.get("action") == "refresh":
        selected = [ticker for ticker in selected if ticker in available_tickers]
    if not selected:
        selected = default_selection

    benchmark_names = list(BENCHMARKS)
    benchmark_name = request.values.get("benchmark", benchmark_names[0])
    if benchmark_name not in BENCHMARKS:
        benchmark_name = benchmark_names[0]
    objective_names = list(OBJECTIVES)
    objective_name = request.values.get("objective", objective_names[0])
    if objective_name not in OBJECTIVES:
        objective_name = objective_names[0]
    risk_free_value = request.values.get("risk_free_rate", "2.0")
    start_value = request.values.get("start_date", default_start.isoformat())
    views_values = {
        ticker: request.values.get(f"view_{ticker}", _DEFAULT_VIEW)
        for ticker in selected
    }
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            selected = selected_tickers()
            custom = custom_tickers()
            allowed = set(ACTIVOS_HRP.values()) | custom
            invalid = [ticker for ticker in selected if ticker not in allowed]
            if invalid:
                raise ValueError("Activos no disponibles: " + ", ".join(invalid))
            if len(selected) < 2:
                raise ValueError(
                    "Selecciona al menos dos activos para calcular el portafolio."
                )
            objective_name = selected_option(
                "objective", OBJECTIVES, objective_names[0]
            )
            benchmark_name = selected_option(
                "benchmark", BENCHMARKS, benchmark_names[0]
            )
            risk_free_rate_pct = parse_bounded_float(
                "risk_free_rate",
                "2.0",
                0,
                100,
                "La tasa libre de riesgo debe ser numérica.",
                "La tasa libre de riesgo debe estar entre 0% y 100%.",
            )
            risk_free_value = str(risk_free_rate_pct)
            views = {}
            for ticker in selected:
                view_pct = parse_bounded_float(
                    f"view_{ticker}",
                    _DEFAULT_VIEW,
                    -100,
                    1000,
                    f"La view anual de {ticker} debe ser numérica.",
                    f"La view anual de {ticker} debe estar entre -100% y 1000%.",
                )
                views[ticker] = view_pct / 100
                views_values[ticker] = str(view_pct)
            start_date = parse_start_date("start_date", default_start)
            names = {ticker: label for label, ticker in universe.items()}
            names.update({ticker: ticker for ticker in custom})
            try:
                result = run_black_litterman(
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
                    download_name=REPORT_FILENAME,
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
                    f"El Sharpe móvil requiere al menos {ROLLING_SHARPE_PERIOD} "
                    "retornos alineados; los demás resultados están disponibles.",
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
