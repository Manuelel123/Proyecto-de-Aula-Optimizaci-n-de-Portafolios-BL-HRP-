"""JSON endpoints of the price explorer: full history and OHLCV on demand."""

import logging
from datetime import date

from flask import jsonify, request
from yfinance.exceptions import YFRateLimitError

from optimizacion_portafolios.data.market_data import DOWNLOAD_ERRORS
from optimizacion_portafolios.web.common.errors import RATE_LIMIT_MESSAGE
from optimizacion_portafolios.web.history import bp
from optimizacion_portafolios.web.history.services import (
    FULL_HISTORY_START,
    build_asset_history,
    build_asset_ohlc,
    normalize_ticker,
    parse_day,
)

logger = logging.getLogger(__name__)


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _download(build, ticker: str, failure: str, *args):
    """Run a download-backed builder, mapping provider failures to JSON errors."""
    try:
        return build(ticker, *args), None
    except YFRateLimitError:
        return None, _error(RATE_LIMIT_MESSAGE, 503)
    except (*DOWNLOAD_ERRORS, ValueError):
        logger.exception("Download failed for %s", ticker)
        return None, _error(failure, 502)


def _cached(payload):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "private, max-age=900"
    return response


@bp.get("/api/historial")
def asset_history():
    try:
        ticker = normalize_ticker(request.args.get("ticker"))
    except ValueError as error:
        return _error(str(error), 400)
    history, failure = _download(
        build_asset_history, ticker, f"No se pudo descargar el historial de {ticker}."
    )
    if failure is not None:
        return failure
    if history is None:
        return _error(f"No hay precios históricos para {ticker}.", 404)
    return _cached(history)


@bp.get("/api/ohlc")
def asset_ohlc():
    """Daily OHLCV of one asset for bars, candles and volume (``?ticker=&start=&end=``)."""
    try:
        ticker = normalize_ticker(request.args.get("ticker"))
        start = parse_day(request.args.get("start"), FULL_HISTORY_START)
        end = parse_day(request.args.get("end"), date.today())
        if start > end:
            raise ValueError("La fecha inicial es posterior a la final.")
    except ValueError as error:
        return _error(str(error), 400)
    ohlc, failure = _download(
        build_asset_ohlc,
        ticker,
        f"No se pudieron descargar los precios OHLC de {ticker}.",
        start,
        end,
    )
    if failure is not None:
        return failure
    if ohlc is None:
        return _error(f"No hay precios OHLC para {ticker} en ese periodo.", 404)
    return _cached(ohlc)
