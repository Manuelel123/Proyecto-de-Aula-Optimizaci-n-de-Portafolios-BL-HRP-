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


# Share of quotes that must carry a full open/high/low/close for bars and
# candles to be offered, and share of them with a real intraday range
# (high > low). Funds and computed indices often publish one value per day
# repeated as open/high/low/close: candles would be flat dashes.
MIN_OHLC_COVERAGE = 0.9
MIN_RANGE_SHARE = 0.2


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame | None:
    columns = ("open", "high", "low", "close", "volume")
    values = frame.reindex(columns=list(columns)).apply(pd.to_numeric, errors="coerce")
    values = values.astype(float).replace([float("inf"), float("-inf")], float("nan"))
    try:
        values.index = pd.DatetimeIndex(pd.to_datetime(values.index)).tz_localize(None).normalize()
    except (TypeError, ValueError):
        return None
    values = values[~values.index.isna()].sort_index()
    values = values[~values.index.duplicated(keep="last")]
    values = values[values["close"] > 0]
    return None if values.empty else values


def _rounded(values: pd.Series) -> list:
    return [round(float(value), 6) for value in values.to_numpy()]


def ohlc_entry(ticker: str, frame: pd.DataFrame) -> dict | None:
    """Columnar OHLCV entry for bars/candles/volume, or None without closes.

    Keys: ``ticker``, ``time``, ``close`` and, only when the provider really
    publishes them, ``open``/``high``/``low`` (``ohlc`` true) and ``volume``
    (``hasVolume`` true; days without volume are null). When a field is not
    available the matching ``ohlcReason``/``volumeReason`` explains why in
    Spanish; nothing is estimated from the closes.

    With OHLC, days missing open/high/low are dropped (at most
    ``1 - MIN_OHLC_COVERAGE`` of them) and high/low are widened to contain
    open and close, absorbing rounding of adjusted prices.
    """
    values = _clean_frame(frame)
    if values is None:
        return None
    prices = values[["open", "high", "low", "close"]]
    complete = (prices > 0).all(axis=1)
    coverage = float(complete.mean())
    full = values.loc[complete]
    with_range = float((full["high"] > full["low"]).mean()) if not full.empty else 0.0

    reason = None
    if coverage == 0:
        reason = (
            f"El proveedor solo publica precios de cierre para {ticker}: "
            "las barras y las velas necesitan apertura, máximo y mínimo."
        )
    elif coverage < MIN_OHLC_COVERAGE:
        reason = (
            f"El proveedor solo publica apertura, máximo y mínimo en el "
            f"{coverage:.0%} de las cotizaciones de {ticker}; "
            "las barras y las velas quedarían incompletas."
        )
    elif with_range < MIN_RANGE_SHARE:
        reason = (
            f"{ticker} publica un solo valor por día (apertura, máximo y mínimo "
            "iguales al cierre), como los fondos mutuos y algunos índices: "
            "las barras y las velas no aportarían información."
        )
    has_ohlc = reason is None
    rows = full if has_ohlc else values

    entry: dict = {"ticker": ticker, "time": [day.strftime("%Y-%m-%d") for day in rows.index]}
    if has_ohlc:
        high = rows[["high", "open", "close"]].max(axis=1)
        low = rows[["low", "open", "close"]].min(axis=1)
        entry.update(open=_rounded(rows["open"]), high=_rounded(high), low=_rounded(low))
    entry["close"] = _rounded(rows["close"])
    entry["ohlc"] = has_ohlc
    entry["ohlcReason"] = reason

    volume = rows["volume"].where(rows["volume"] >= 0)
    has_volume = bool((volume > 0).any())
    entry["hasVolume"] = has_volume
    entry["volume"] = (
        [None if pd.isna(value) else round(float(value), 2) for value in volume.to_numpy()]
        if has_volume
        else None
    )
    entry["volumeReason"] = (
        None if has_volume else f"El proveedor no reporta volumen negociado para {ticker}."
    )
    return entry
