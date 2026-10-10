"""Price series serialized for the Lightweight Charts price explorer."""

import json
from math import isfinite

import pandas as pd
from markupsafe import Markup


def _script_safe(text: str) -> Markup:
    """Escape JSON so it can sit inside <script type="application/json">."""
    return Markup(
        text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def _series_entry(ticker: str, name: str, prices: pd.Series) -> dict | None:
    """Columnar entry with strictly ascending, unique daily dates.

    Lightweight Charts rejects unsorted or repeated times, so the index is
    normalized to calendar days, sorted, and the last quote of a day is kept.
    Non-numeric, infinite and non-positive prices are dropped.
    """
    values = pd.to_numeric(pd.Series(prices), errors="coerce").astype(float)
    try:
        values.index = pd.DatetimeIndex(pd.to_datetime(values.index)).tz_localize(None).normalize()
    except (TypeError, ValueError):
        return None
    values = values[values.map(isfinite) & (values > 0)]
    values = values[~values.index.isna()].sort_index()
    values = values[~values.index.duplicated(keep="last")]
    if values.empty:
        return None
    return {
        "ticker": ticker,
        "name": name,
        "time": [day.strftime("%Y-%m-%d") for day in values.index],
        "value": [round(float(value), 6) for value in values.to_numpy()],
    }


def price_series_payload(
    raw_prices: pd.DataFrame,
    names: dict[str, str] | None = None,
    benchmark: pd.Series | None = None,
) -> Markup | None:
    """Return the price explorer JSON, or None when there is nothing to plot.

    Format (columnar, one entry per ticker, NaNs dropped per ticker so stocks
    mixed with crypto do not show flat weekends)::

        {"series": [{"ticker": "AAPL", "name": "Apple (AAPL)",
                     "time": ["2025-10-09", ...], "value": [227.48, ...]}],
         "benchmark": {"ticker": "SPY", "name": "SPY", "time": [...],
                       "value": [...]} | null}

    Use the prices *before* forward-filling/aligning them across assets.
    """
    names = names or {}
    series = [
        entry
        for ticker in raw_prices.columns
        if (entry := _series_entry(str(ticker), str(names.get(ticker, ticker)), raw_prices[ticker]))
    ]
    if not series:
        return None
    # A benchmark that is also one of the assets keeps both entries: the
    # explorer overlays it in base 100 only when another asset is selected.
    benchmark_entry = None
    if benchmark is not None:
        label = str(benchmark.name) if benchmark.name is not None else "Benchmark"
        benchmark_entry = _series_entry(label, str(names.get(label, label)), benchmark)
    payload = {"series": series, "benchmark": benchmark_entry}
    return _script_safe(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
