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
    CSV_FILENAME,
    CURRENT_PORTFOLIO,
    DEFAULT_VIEW,
    LOADING_STEPS,
    OBJECTIVE_GROUPS,
    OBJECTIVE_HELP,
    OBJECTIVES,
    REPORT_FILENAME,
    ROLLING_SHARPE_PERIOD,
    UNCONSTRAINED_OBJECTIVE,
    VIEW_CONFIDENCE,
    WINDOW_YEARS,
    default_selection,
    display_date,
    run_black_litterman,
)
from optimizacion_portafolios.web.common.forms import (
    custom_tickers,
    normalize_ticker,
    parse_bounded_float,
    selected_option,
    selected_tickers,
    universes_payload,
)

logger = logging.getLogger(__name__)
# Every known ticker -> readable label ("Apple (AAPL)"); unknown tickers show as-is.
_CATALOG_LABELS = {ticker: label for label, ticker in ACTIVOS_HRP.items()}


def _valid_custom_tickers() -> tuple[list[str], list[str]]:
    """Typed custom tickers split into (valid, invalid), preserving order."""
    valid, invalid = [], []
    for raw in request.form.get("custom_tickers", "").split(","):
        if not raw.strip():
            continue
        try:
            valid.append(normalize_ticker(raw))
        except ValueError:
            invalid.append(raw.strip())
    return list(dict.fromkeys(valid)), invalid


@bp.route("/black-litterman", methods=["GET", "POST"])
def black_litterman():
    today = date.today()
    # The model always uses a fixed two-year window; a submitted start_date is ignored.
    window_start = date_years_ago(today, WINDOW_YEARS)
    universe_names = list(UNIVERSOS_BLACK_LITTERMAN)
    universe_name = request.values.get("universe", CURRENT_PORTFOLIO)
    if universe_name not in UNIVERSOS_BLACK_LITTERMAN:
        flash("Selecciona un universo de activos válido.", "error")
        universe_name = CURRENT_PORTFOLIO
    universe = UNIVERSOS_BLACK_LITTERMAN[universe_name]
    universe_labels = {ticker: label for label, ticker in universe.items()}
    available_tickers = list(universe.values())
    selected = request.form.getlist("tickers") if request.method == "POST" else []
    if request.method == "POST" and request.form.get("action") == "refresh":
        selected = [ticker for ticker in selected if ticker in available_tickers]
    if not selected:
        selected = default_selection(universe_name, available_tickers)
    custom_valid, custom_invalid = (
        _valid_custom_tickers() if request.method == "POST" else ([], [])
    )
    if request.form.get("action") == "refresh" and custom_invalid:
        flash("Símbolos no válidos ignorados: " + ", ".join(custom_invalid), "error")
    # Every asset that enters the model (checked + custom) gets a visible view.
    view_tickers = list(dict.fromkeys([*selected, *custom_valid]))

    benchmark_names = list(BENCHMARKS)
    benchmark_name = request.values.get("benchmark", benchmark_names[0])
    if benchmark_name not in BENCHMARKS:
        benchmark_name = benchmark_names[0]
    objective_names = list(OBJECTIVES)
    objective_name = request.values.get("objective", objective_names[0])
    if objective_name not in OBJECTIVES:
        objective_name = objective_names[0]
    risk_free_value = request.values.get("risk_free_rate", "2.0")
    views_values = {
        ticker: request.values.get(f"view_{ticker}", DEFAULT_VIEW)
        for ticker in view_tickers
    }
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            selected = selected_tickers()
            view_tickers = selected
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
            unseen = [
                ticker for ticker in selected if f"view_{ticker}" not in request.form
            ]
            if unseen:
                views_values.update(dict.fromkeys(unseen, DEFAULT_VIEW))
                raise ValueError(
                    "Define la view anual de: "
                    + ", ".join(unseen)
                    + ". Se propuso "
                    + DEFAULT_VIEW
                    + "%; revísala y vuelve a calcular."
                )
            views = {}
            for ticker in selected:
                view_pct = parse_bounded_float(
                    f"view_{ticker}",
                    DEFAULT_VIEW,
                    -100,
                    1000,
                    f"La view anual de {ticker} debe ser numérica.",
                    f"La view anual de {ticker} debe estar entre -100% y 1000%.",
                )
                views[ticker] = view_pct / 100
                views_values[ticker] = str(view_pct)
            names = {**_CATALOG_LABELS, **universe_labels}
            try:
                result = run_black_litterman(
                    selected,
                    views,
                    objective_name,
                    risk_free_rate_pct / 100,
                    BENCHMARKS[benchmark_name],
                    window_start,
                    names,
                    request.form.get("action") == "download_report",
                    benchmark_label=benchmark_name,
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
            result = None
            flash(str(error), "error")

    return render_template(
        "black_litterman/black_litterman.html",
        window_start=display_date(window_start),
        window_years=WINDOW_YEARS,
        universe_names=universe_names,
        universe_name=universe_name,
        universes=universes_payload(
            UNIVERSOS_BLACK_LITTERMAN, default_selection=default_selection
        ),
        available_tickers=available_tickers,
        asset_labels=universe_labels,
        catalog_labels={**_CATALOG_LABELS, **universe_labels},
        selected_tickers=selected,
        custom_tickers_value=request.form.get("custom_tickers", ""),
        view_tickers=view_tickers,
        view_confidence=VIEW_CONFIDENCE,
        default_view=DEFAULT_VIEW,
        unconstrained_objective=UNCONSTRAINED_OBJECTIVE,
        benchmark_names=benchmark_names,
        benchmark_name=benchmark_name,
        benchmark_ticker=BENCHMARKS[benchmark_name],
        objective_groups=OBJECTIVE_GROUPS,
        objective_help=OBJECTIVE_HELP,
        objective_name=objective_name,
        risk_free_value=risk_free_value,
        views_values=views_values,
        equilibrium=result["prior_returns"] if result else {},
        loading_steps=list(LOADING_STEPS),
        csv_filename=CSV_FILENAME,
        result=result,
    )
