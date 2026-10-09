"""Input parsing and validation shared by Flask application modules."""

import re
from datetime import date
from math import isfinite

from flask import request


_TICKER_PATTERN = re.compile(r"^[A-Z0-9^=.\-]{1,24}$")


def normalize_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not _TICKER_PATTERN.fullmatch(ticker):
        raise ValueError(f"El símbolo {ticker!r} no es válido.")
    return ticker


def custom_tickers(field: str = "custom_tickers") -> set[str]:
    """Raw comma-separated tickers typed by the user (normalized case only)."""
    return {
        ticker.strip().upper()
        for ticker in request.form.get(field, "").split(",")
        if ticker.strip()
    }


def selected_tickers(field: str = "tickers") -> list[str]:
    """Checked tickers followed by custom tickers, validated and de-duplicated."""
    selected = [normalize_ticker(value) for value in request.form.getlist(field)]
    custom = request.form.get("custom_tickers", "")
    selected.extend(normalize_ticker(ticker) for ticker in custom.split(",") if ticker.strip())
    return list(dict.fromkeys(selected))


def parse_start_date(field: str, default: date) -> date:
    value = request.form.get(field, "").strip()
    if not value:
        return default
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("La fecha inicial no tiene un formato válido.") from error
    if parsed > date.today():
        raise ValueError("La fecha inicial no puede ser posterior a hoy.")
    return parsed


def parse_bounded_float(
    field: str,
    default: str,
    minimum: float,
    maximum: float,
    numeric_error: str,
    range_error: str,
) -> float:
    try:
        value = float(request.form.get(field, default))
    except ValueError as error:
        raise ValueError(numeric_error) from error
    if not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(range_error)
    return value


def selected_option(field: str, choices: dict, default: str) -> str:
    value = request.form.get(field, default)
    if value not in choices:
        raise ValueError("La opción seleccionada no es válida.")
    return value
