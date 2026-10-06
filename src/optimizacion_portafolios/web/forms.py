"""Input parsing and validation shared by the Flask blueprints."""

import re
from datetime import date

from flask import request


_TICKER_PATTERN = re.compile(r"^[A-Z0-9^=.\-]{1,24}$")


def normalize_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not _TICKER_PATTERN.fullmatch(ticker):
        raise ValueError(f"El símbolo {ticker!r} no es válido.")
    return ticker


def selected_tickers(field: str = "tickers") -> list[str]:
    selected = [normalize_ticker(value) for value in request.form.getlist(field)]
    custom = request.form.get("custom_tickers", "")
    selected.extend(normalize_ticker(ticker) for ticker in custom.split(",") if ticker.strip())
    unique = list(dict.fromkeys(selected))
    return unique


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


def selected_option(field: str, choices: dict, default: str) -> str:
    value = request.form.get(field, default)
    if value not in choices:
        raise ValueError("La opción seleccionada no es válida.")
    return value
