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
    prices = prices.dropna()
    prices = prices[prices.map(lambda value: isfinite(float(value)))]
    if prices.empty:
        return None
    return {
        "ticker": ticker,
        "name": name,
        "time": [pd.Timestamp(day).strftime("%Y-%m-%d") for day in prices.index],
        "value": [round(float(value), 6) for value in prices.to_numpy()],
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
        if (entry := _series_entry(str(ticker), names.get(ticker, str(ticker)), raw_prices[ticker]))
    ]
    if not series:
        return None
    benchmark_entry = None
    if benchmark is not None:
        label = str(benchmark.name) if benchmark.name is not None else "Benchmark"
        benchmark_entry = _series_entry(label, names.get(label, label), benchmark)
    payload = {"series": series, "benchmark": benchmark_entry}
    return _script_safe(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
