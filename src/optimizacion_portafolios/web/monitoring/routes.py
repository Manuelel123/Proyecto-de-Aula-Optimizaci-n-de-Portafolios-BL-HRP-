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
    universes_payload,
)
from optimizacion_portafolios.web.monitoring import bp
from optimizacion_portafolios.web.monitoring.services import (
    build_fundamental_analysis,
    build_price_analysis,
)

logger = logging.getLogger(__name__)
_VIEWS = ("portfolio", "options", "fundamental")
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


def _requested_portfolio() -> str:
    """Monitored portfolio from ``universe`` (shared select) or ``portfolio``."""
    names = list(PORTAFOLIOS_MONITOREO)
    name = request.values.get("universe") or request.values.get("portfolio")
    if name is None:
        return names[0]
    if name not in PORTAFOLIOS_MONITOREO:
        flash("Selecciona un portafolio válido.", "error")
        return names[0]
    return name


def _checked_assets(available: list[str]) -> list[str]:
    """Chips to render checked; every asset on first load or portfolio refresh."""
    if request.method == "POST":
        checked = [t for t in request.form.getlist("tickers") if t in available]
        if checked or request.form.get("action") != "refresh":
            return checked
    return list(available)


def _company_groups() -> list[dict]:
    """Companies for the fundamental view, grouped by monitored portfolio."""
    seen: set[str] = set()
    groups = []
    for name, assets in PORTAFOLIOS_MONITOREO.items():
        options = [
            {"ticker": ticker, "label": label}
            for label, ticker in assets.items()
            if ticker not in seen and not seen.add(ticker)
        ]
        groups.append({"name": name, "options": options})
    return groups


def _run_fundamental(ticker: str) -> dict | None:
    if ticker not in _MONITORED_TICKERS:
        raise ValueError("El activo no pertenece a los portafolios monitoreados.")
    try:
        fundamental = build_fundamental_analysis(ticker)
    except (requests.RequestException, TimeoutError, YFException) as error:
        logger.exception("Yahoo Finance fundamental lookup failed for %s", ticker)
        raise ValueError(
            _yahoo_error_message(
                error, f"No se pudo consultar Yahoo Finance para {ticker}"
            )
        ) from error
    if fundamental is None:
        flash(f"Yahoo Finance no devolvió información para {ticker}.", "warning")
        return None
    return {"fundamental": fundamental}


def _run_price_analysis(view, portfolio_tickers, start_date, period) -> dict:
    tickers = _tickers_for_view(view, set(portfolio_tickers))
    try:
        result = build_price_analysis(view, tickers, start_date, period)
    except (requests.RequestException, TimeoutError, YFException) as error:
        logger.exception("Yahoo Finance request failed during asset monitoring")
        raise ValueError(
            _yahoo_error_message(error, "No se pudieron descargar los precios")
        ) from error
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
    return result


@bp.route("/monitoring", methods=["GET", "POST"])
def monitoring():
    view = request.values.get("view", "portfolio")
    if view not in _VIEWS:
        flash("La sección de monitoreo solicitada no existe.", "error")
        view = "portfolio"

    today = date.today()
    default_start = date_years_ago(today)
    portfolio_name = _requested_portfolio()
    portfolio_tickers = list(PORTAFOLIOS_MONITOREO[portfolio_name].values())
    option_tickers = list(ACTIVOS_OPCIONES.values())
    available = option_tickers if view == "options" else portfolio_tickers
    first_ticker = next(iter(PORTAFOLIOS_MONITOREO.values()))
    selected_ticker = request.values.get("ticker", "").strip().upper() or next(
        iter(first_ticker.values())
    )
    period = request.values.get("period", _DEFAULT_PERIOD)
    if period not in PERIODOS_VOLATILIDAD:
        period = _DEFAULT_PERIOD
    start_value = request.values.get("start_date", default_start.isoformat())
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            if view == "fundamental":
                result = _run_fundamental(selected_ticker)
            else:
                start_date = parse_start_date("start_date", default_start)
                if view == "options":
                    period = selected_option(
                        "period", PERIODOS_VOLATILIDAD, _DEFAULT_PERIOD
                    )
                result = _run_price_analysis(
                    view, portfolio_tickers, start_date, period
                )
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    return render_template(
        "monitoring/monitoring.html",
        view=view,
        today=today.isoformat(),
        default_start=start_value,
        portfolio_names=list(PORTAFOLIOS_MONITOREO),
        portfolio_name=portfolio_name,
        universes_payload=universes_payload(PORTAFOLIOS_MONITOREO),
        available_tickers=available,
        checked_tickers=_checked_assets(available),
        custom_tickers=request.values.get("custom_tickers", ""),
        company_groups=_company_groups(),
        period_names=list(PERIODOS_VOLATILIDAD),
        period=period,
        selected_ticker=selected_ticker,
        result=result,
    )
