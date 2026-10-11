"""Full-history view model: every available price and its returns matrix."""

import json
import re
from datetime import date

from optimizacion_portafolios.data.catalogs import BENCHMARKS, GRUPOS_ACTIVOS
from optimizacion_portafolios.data.market_data import download_prices
from optimizacion_portafolios.web.common.charts import returns_matrix_chart
from optimizacion_portafolios.web.common.forms import asset_labels
from optimizacion_portafolios.web.common.price_series import price_series_payload

# Yahoo Finance returns each asset from its first quote (e.g. ^GSPC from 1927).
FULL_HISTORY_START = date(1900, 1, 1)
_TICKER_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=\-]{0,19}$")


def normalize_ticker(raw: str | None) -> str:
    """Uppercase ticker or ValueError when it is not a plausible symbol."""
    ticker = (raw or "").strip().upper()
    if not _TICKER_PATTERN.match(ticker):
        raise ValueError("Ticker no válido.")
    return ticker


def _display_names() -> dict[str, str]:
    catalog = dict(BENCHMARKS)
    for assets in GRUPOS_ACTIVOS.values():
        catalog.update({label: ticker for label, ticker in assets.items() if label not in catalog})
    return asset_labels(catalog)


def build_asset_history(ticker: str) -> dict | None:
    """Every available daily price of ``ticker`` plus its returns matrix.

    Returns None when the provider has no prices for the ticker. Keys:
    ``ticker``, ``name``, ``start``, ``end``, ``observations``, ``years``,
    ``prices`` (one price-explorer series entry: ticker, name, time, value)
    and ``matrix`` (Plotly figure of ``returns_matrix_chart``, or None).
    """
    prices = download_prices((ticker,), FULL_HISTORY_START, date.today())
    if prices.empty or ticker not in prices.columns:
        return None
    series = prices[ticker].dropna()
    series = series[series > 0]
    if series.empty:
        return None
    name = _display_names().get(ticker, ticker)
    payload = price_series_payload(series.to_frame(ticker), {ticker: name})
    if payload is None:
        return None
    matrix = returns_matrix_chart(
        {ticker: series.pct_change(fill_method=None).dropna()}, {ticker: name}
    )
    return {
        "ticker": ticker,
        "name": name,
        "start": series.index.min().date().isoformat(),
        "end": series.index.max().date().isoformat(),
        "observations": int(series.size),
        "years": int(series.index.max().year - series.index.min().year + 1),
        "prices": json.loads(str(payload))["series"][0],
        "matrix": json.loads(str(matrix)) if matrix is not None else None,
    }
