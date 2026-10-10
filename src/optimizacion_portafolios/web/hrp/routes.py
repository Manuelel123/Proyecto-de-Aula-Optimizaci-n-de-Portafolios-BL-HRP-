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
    parse_bounded_float,
    parse_optional_bounded_float,
    parse_start_date,
    selected_option,
    selected_tickers,
    universes_payload,
)
from optimizacion_portafolios.web.hrp import bp
from optimizacion_portafolios.web.hrp.services import (
    REPORT_FILENAME,
    ROLLING_SHARPE_PERIOD,
    WeightBounds,
    asset_name,
    build_hrp_report,
    run_hrp,
)

logger = logging.getLogger(__name__)
# Catalog labels per ticker, e.g. "AAPL" -> "Apple (AAPL)". Some catalogs use
# the ticker itself as label, so a descriptive label wins over a bare ticker.
_ASSET_LABELS: dict[str, str] = {}
for _label, _ticker in ACTIVOS_HRP.items():
    if _ASSET_LABELS.get(_ticker, _ticker) == _ticker:
        _ASSET_LABELS[_ticker] = _label
_DEFAULT_UNIVERSE = "Portafolio actual"
_DEFAULT_MIN_WEIGHT = "0"
_DEFAULT_MAX_WEIGHT = "100"


def _percent_field(field: str, label: str, default: str) -> float:
    return parse_bounded_float(
        field,
        default,
        0.0,
        100.0,
        f"El {label} debe ser numérico.",
        f"El {label} debe estar entre 0 % y 100 %.",
    ) / 100


def _is_custom_limit(value: str, default: float) -> bool:
    """True when a global limit typed in percent differs from its default."""
    try:
        return abs(float(value) - default) > 1e-9
    except (TypeError, ValueError):
        return bool(str(value).strip())


def _asset_limits(prefix: str, label: str, tickers: list[str]) -> dict[str, float]:
    limits = {}
    for ticker in tickers:
        value = parse_optional_bounded_float(
            f"{prefix}_{ticker}",
            0.0,
            100.0,
            f"El {label} de {ticker} debe ser numérico.",
            f"El {label} de {ticker} debe estar entre 0 % y 100 %.",
        )
        if value is not None:
            limits[ticker] = value / 100
    return limits


def _weight_bounds(tickers: list[str]) -> WeightBounds:
    """Global and per-asset weight limits typed in percent (empty = global)."""
    return WeightBounds(
        min_weight=_percent_field("min_weight", "peso mínimo", _DEFAULT_MIN_WEIGHT),
        max_weight=_percent_field("max_weight", "peso máximo", _DEFAULT_MAX_WEIGHT),
        asset_min_weights=_asset_limits("min", "peso mínimo", tickers),
        asset_max_weights=_asset_limits("max", "peso máximo", tickers),
    )


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
            bounds = _weight_bounds(selected)
            download_requested = request.form.get("action") == "download_report"
            try:
                if download_requested:
                    report, missing = build_hrp_report(
                        selected, benchmark_ticker, start_date, bounds
                    )
                else:
                    result = run_hrp(
                        selected, benchmark_ticker, start_date, bounds, _ASSET_LABELS
                    )
                    missing = result["missing"]
            except (requests.RequestException, TimeoutError, YFException) as error:
                logger.exception("Yahoo Finance request failed during HRP optimization")
                raise ValueError(f"No se pudieron descargar los datos: {error}") from error
            if missing and download_requested:
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
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    asset_limits = {
        ticker: {
            "min": request.form.get(f"min_{ticker}", "").strip(),
            "max": request.form.get(f"max_{ticker}", "").strip(),
        }
        for ticker in selected
    }
    min_weight = request.values.get("min_weight", _DEFAULT_MIN_WEIGHT)
    max_weight = request.values.get("max_weight", _DEFAULT_MAX_WEIGHT)
    has_asset_limits = any(
        limits["min"] or limits["max"] for limits in asset_limits.values()
    )
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
        min_weight=min_weight,
        max_weight=max_weight,
        asset_limits=asset_limits,
        has_asset_limits=has_asset_limits,
        limits_open=has_asset_limits
        or _is_custom_limit(min_weight, 0.0)
        or _is_custom_limit(max_weight, 100.0),
        asset_labels={ticker: _ASSET_LABELS.get(ticker, ticker) for ticker in available_tickers},
        asset_names={ticker: asset_name(ticker, _ASSET_LABELS.get(ticker)) for ticker in selected},
        universes=universes_payload(UNIVERSOS_HRP),
        custom_tickers_value=request.values.get("custom_tickers", ""),
        rolling_sharpe_period=ROLLING_SHARPE_PERIOD,
        result=result,
    )
