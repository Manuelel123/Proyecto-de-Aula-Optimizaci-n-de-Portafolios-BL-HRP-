"""JSON endpoint used by the price explorer's "Historial completo" switch."""

import logging

import requests
from flask import jsonify, request
from yfinance.exceptions import YFException, YFRateLimitError

from optimizacion_portafolios.web.history import bp
from optimizacion_portafolios.web.history.services import (
    build_asset_history,
    normalize_ticker,
)

logger = logging.getLogger(__name__)


def _error(message: str, status: int):
    return jsonify({"error": message}), status


@bp.get("/api/historial")
def asset_history():
    try:
        ticker = normalize_ticker(request.args.get("ticker"))
    except ValueError as error:
        return _error(str(error), 400)
    try:
        history = build_asset_history(ticker)
    except YFRateLimitError:
        return _error(
            "Yahoo Finance limitó temporalmente las consultas. "
            "Espera unos minutos y vuelve a intentar.",
            503,
        )
    except (requests.RequestException, TimeoutError, YFException, ValueError):
        logger.exception("Full history download failed for %s", ticker)
        return _error(f"No se pudo descargar el historial de {ticker}.", 502)
    if history is None:
        return _error(f"No hay precios históricos para {ticker}.", 404)
    response = jsonify(history)
    response.headers["Cache-Control"] = "private, max-age=900"
    return response
