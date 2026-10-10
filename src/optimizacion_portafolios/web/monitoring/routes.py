"""Asset monitoring and company-fundamentals HTTP routes."""

import logging
from datetime import date

import numpy as np
import requests
from flask import flash, render_template, request
from yfinance.exceptions import YFException, YFRateLimitError

from optimizacion_portafolios.analytics.statistics import date_years_ago
from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_OPCIONES,
    PERIODOS_VOLATILIDAD,
    PORTAFOLIOS_MONITOREO,
)
from optimizacion_portafolios.web.common.forms import (
    parse_start_date,
    selected_option,
    selected_tickers,
)
from optimizacion_portafolios.web.monitoring import bp
from optimizacion_portafolios.web.monitoring.services import (
    build_fundamental_analysis,
    build_price_analysis,
)

logger = logging.getLogger(__name__)
_VIEWS = {"options", "portfolio", "fundamental"}
_DEFAULT_PERIOD = "Mensual"
_MONITORED_TICKERS = set().union(
    *(set(portfolio.values()) for portfolio in PORTAFOLIOS_MONITOREO.values())
)


def _yahoo_error_message(error: Exception, context: str) -> str:
    if isinstance(error, YFRateLimitError):
        return (
            "Yahoo Finance limitó temporalmente las consultas. "
            "Espera unos minutos y vuelve a intentar."
        )
    return f"{context}: {error}"


def _tickers_for_view(view: str, portfolio_tickers: set[str]) -> list[str]:
    """Checked and custom tickers allowed for the active view."""
    selected = selected_tickers()
    if view == "portfolio":
        # Keep this portfolio's assets and custom tickers; drop assets that were
        # checked under a different monitored portfolio.
        selected = [
            ticker
            for ticker in selected
            if ticker in portfolio_tickers or ticker not in _MONITORED_TICKERS
        ]
    tickers = list(dict.fromkeys(selected))
    if not tickers:
        raise ValueError("Selecciona al menos un activo para monitorear.")
    return tickers


@bp.route("/monitoring", methods=["GET", "POST"])
def monitoring():
    view = request.values.get("view", "portfolio")
    if view not in _VIEWS:
        flash("La sección de monitoreo solicitada no existe.", "error")
        view = "portfolio"

    today = date.today()
    default_start = date_years_ago(today)
    portfolio_names = list(PORTAFOLIOS_MONITOREO)
    portfolio_name = request.values.get("portfolio", portfolio_names[0])
    if portfolio_name not in PORTAFOLIOS_MONITOREO:
        flash("Selecciona un portafolio válido.", "error")
        portfolio_name = portfolio_names[0]
    portfolio_tickers = list(PORTAFOLIOS_MONITOREO[portfolio_name].values())
    selected_ticker = request.values.get("ticker", portfolio_tickers[0])
    selected_volatility_ticker = request.values.get(
        "volatility_ticker", portfolio_tickers[0]
    )
    if selected_volatility_ticker not in portfolio_tickers:
        selected_volatility_ticker = portfolio_tickers[0]
    period = request.values.get("period", _DEFAULT_PERIOD)
    if period not in PERIODOS_VOLATILIDAD:
        period = _DEFAULT_PERIOD
    start_value = request.values.get("start_date", default_start.isoformat())
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            start_date = parse_start_date("start_date", default_start)
            if view == "fundamental":
                selected_ticker = request.form.get("ticker", "").strip().upper()
                if selected_ticker not in portfolio_tickers:
                    raise ValueError("El activo no pertenece al portafolio seleccionado.")
                try:
                    fundamental = build_fundamental_analysis(selected_ticker)
                except (requests.RequestException, TimeoutError, YFException) as error:
                    logger.exception(
                        "Yahoo Finance fundamental lookup failed for %s", selected_ticker
                    )
                    raise ValueError(
                        _yahoo_error_message(
                            error,
                            f"No se pudo consultar Yahoo Finance para {selected_ticker}",
                        )
                    ) from error
                if fundamental is None:
                    flash(
                        f"Yahoo Finance no devolvió información para {selected_ticker}.",
                        "warning",
                    )
                else:
                    result = {"fundamental": fundamental}
            else:
                tickers = _tickers_for_view(view, set(portfolio_tickers))
                if view == "options":
                    period = selected_option(
                        "period", PERIODOS_VOLATILIDAD, _DEFAULT_PERIOD
                    )
                try:
                    result = build_price_analysis(
                        view,
                        tickers,
                        start_date,
                        period,
                        request.form.get("volatility_ticker", ""),
                    )
                except (requests.RequestException, TimeoutError, YFException) as error:
                    logger.exception(
                        "Yahoo Finance request failed during asset monitoring"
                    )
                    raise ValueError(
                        f"No se pudieron descargar los precios: {error}"
                    ) from error
                if view == "portfolio":
                    selected_volatility_ticker = result["selected_volatility_ticker"]
                if result["missing"]:
                    flash(
                        "No se recibieron datos para: " + ", ".join(result["missing"]),
                        "warning",
                    )
                if not result["has_period_volatility"]:
                    flash(
                        "No hay periodos completos con suficientes datos para la volatilidad.",
                        "warning",
                    )
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    return render_template(
        "monitoring/monitoring.html",
        view=view,
        today=today.isoformat(),
        default_start=start_value,
        portfolio_names=portfolio_names,
        portfolio_name=portfolio_name,
        portfolio_tickers=portfolio_tickers,
        option_tickers=list(ACTIVOS_OPCIONES.values()),
        period_names=list(PERIODOS_VOLATILIDAD),
        period=period,
        selected_ticker=selected_ticker,
        selected_volatility_ticker=selected_volatility_ticker,
        result=result,
    )
