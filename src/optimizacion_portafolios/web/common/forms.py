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


def parse_optional_bounded_float(
    field: str,
    minimum: float,
    maximum: float,
    numeric_error: str,
    range_error: str,
) -> float | None:
    """Like ``parse_bounded_float`` but an empty or missing field returns ``None``."""
    if not request.form.get(field, "").strip():
        return None
    return parse_bounded_float(
        field, "", minimum, maximum, numeric_error, range_error
    )


def _unique(values) -> list[str]:
    return list(dict.fromkeys(values))


def asset_labels(assets: dict[str, str]) -> dict[str, str]:
    """Map ``ticker -> display label`` from a ``{label: ticker}`` catalog.

    Catalogs such as ``ACTIVOS_HRP`` repeat tickers under several labels
    (``"Apple (AAPL)"`` and ``"AAPL"``); the first descriptive label wins.
    """
    labels: dict[str, str] = {}
    for label, ticker in assets.items():
        if ticker not in labels or (labels[ticker] == ticker and label != ticker):
            labels[ticker] = label
    return labels


def asset_groups(tickers) -> dict[str, str]:
    """Map ``ticker -> display group`` (``GRUPOS_ACTIVOS``) for the asset selector.

    When every ticker belongs to a single catalog group, that group is used for
    all of them (the selector then shows no group headings). Otherwise each
    ticker takes its first matching group, and unknown tickers fall into
    ``"Otros"``. The result preserves the order of ``tickers`` (de-duplicated).
    """
    from optimizacion_portafolios.data.catalogs import GRUPOS_ACTIVOS

    tickers = _unique(tickers)
    members = {group: set(assets.values()) for group, assets in GRUPOS_ACTIVOS.items()}
    for group, values in members.items():
        if tickers and all(ticker in values for ticker in tickers):
            return {ticker: group for ticker in tickers}
    return {
        ticker: next(
            (group for group, values in members.items() if ticker in values),
            "Otros",
        )
        for ticker in tickers
    }


def universes_payload(
    universes: dict[str, dict[str, str]],
    default_selection=None,
) -> dict[str, dict]:
    """Universe catalog for the client-side asset selector (embedded as JSON).

    Format::

        {"<universe name>": {
            "assets": [{"ticker": "AAPL", "label": "Apple (AAPL)", "group": "Acciones"}, ...],
            "default": ["AAPL", ...]   # tickers checked when nothing else applies
        }}

    Tickers are de-duplicated (first occurrence wins) and keep the catalog
    order. ``default_selection(universe_name, tickers) -> list[str]`` decides
    the default checked tickers (all of them when omitted); it receives the
    de-duplicated tickers and must replicate the server rule of the page.
    """
    payload = {}
    for universe_name, assets in universes.items():
        tickers = _unique(assets.values())
        labels = asset_labels(assets)
        groups = asset_groups(tickers)
        default = (
            default_selection(universe_name, tickers) if default_selection else tickers
        )
        payload[universe_name] = {
            "assets": [
                {"ticker": ticker, "label": labels[ticker], "group": groups[ticker]}
                for ticker in tickers
            ],
            "default": [ticker for ticker in _unique(default) if ticker in groups],
        }
    return payload
