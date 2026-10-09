"""Hierarchical risk parity HTTP routes."""

import logging
from datetime import date
from io import BytesIO

import numpy as np
import requests
from flask import flash, render_template, request, send_file
from yfinance.exceptions import YFException

from optimizacion_portafolios.analytics.statistics import date_years_ago
from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_HRP,
    BENCHMARKS,
    UNIVERSOS_HRP,
)
from optimizacion_portafolios.web.common.forms import (
    custom_tickers,
    normalize_ticker,
    parse_start_date,
    selected_option,
    selected_tickers,
)
from optimizacion_portafolios.web.hrp import bp
from optimizacion_portafolios.web.hrp.services import (
    REPORT_FILENAME,
    ROLLING_SHARPE_PERIOD,
    build_hrp_report,
    run_hrp,
)

logger = logging.getLogger(__name__)
_DEFAULT_UNIVERSE = "Portafolio actual"


@bp.route("/hrp", methods=["GET", "POST"])
def hrp():
    today = date.today()
    default_start = date_years_ago(today)
    universe_names = list(UNIVERSOS_HRP)
    universe_name = request.values.get("universe", _DEFAULT_UNIVERSE)
    if universe_name not in UNIVERSOS_HRP:
        flash("Selecciona un universo de activos válido.", "error")
        universe_name = _DEFAULT_UNIVERSE
    available_tickers = list(UNIVERSOS_HRP[universe_name].values())
    selected = request.form.getlist("tickers") if request.method == "POST" else []
    if request.method == "POST" and request.form.get("action") == "refresh":
        selected = [ticker for ticker in selected if ticker in available_tickers]
    if not selected:
        selected = available_tickers

    benchmark_names = list(BENCHMARKS)
    benchmark_name = request.values.get("benchmark", benchmark_names[0])
    if benchmark_name not in BENCHMARKS:
        benchmark_name = benchmark_names[0]
    custom_benchmark = request.values.get("custom_benchmark", "").strip()
    start_value = request.values.get("start_date", default_start.isoformat())
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            selected = selected_tickers()
            allowed = set(ACTIVOS_HRP.values()) | custom_tickers()
            invalid = [ticker for ticker in selected if ticker not in allowed]
            if invalid:
                raise ValueError("Activos no disponibles: " + ", ".join(invalid))
            if len(selected) < 2:
                raise ValueError("Selecciona al menos dos activos para calcular HRP.")
            benchmark_name = selected_option(
                "benchmark", BENCHMARKS, benchmark_names[0]
            )
            benchmark_ticker = (
                normalize_ticker(custom_benchmark)
                if custom_benchmark
                else BENCHMARKS[benchmark_name]
            )
            start_date = parse_start_date("start_date", default_start)
            download_requested = request.form.get("action") == "download_report"
            try:
                if download_requested:
                    report, missing = build_hrp_report(
                        selected, benchmark_ticker, start_date
                    )
                else:
                    result = run_hrp(selected, benchmark_ticker, start_date)
                    missing = result["missing"]
            except (requests.RequestException, TimeoutError, YFException) as error:
                logger.exception("Yahoo Finance request failed during HRP optimization")
                raise ValueError(f"No se pudieron descargar los datos: {error}") from error
            if missing:
                flash(
                    "Yahoo Finance no devolvió datos para: " + ", ".join(missing),
                    "warning",
                )
            if download_requested:
                return send_file(
                    BytesIO(report),
                    mimetype="text/html",
                    as_attachment=True,
                    download_name=REPORT_FILENAME,
                )
            if not result["has_rolling_sharpe"]:
                flash(
                    f"El Sharpe móvil requiere al menos {ROLLING_SHARPE_PERIOD} "
                    "retornos alineados; los demás resultados HRP están disponibles.",
                    "warning",
                )
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    return render_template(
        "hrp/hrp.html",
        today=today.isoformat(),
        default_start=start_value,
        universe_names=universe_names,
        universe_name=universe_name,
        available_tickers=available_tickers,
        selected_tickers=selected,
        benchmark_names=benchmark_names,
        benchmark_name=benchmark_name,
        custom_benchmark=custom_benchmark,
        result=result,
    )
