"""Quote currency of each ticker and daily conversion of prices into USD.

Tracked portfolios are valued in USD. Tickers served by Tiingo (US listings and
``XXX-USD`` crypto pairs) are USD; any other ticker uses the ``currency`` of its
Yahoo Finance profile. Prices in pence (``GBp``/``GBX``) are divided by 100.
Exchange rates come from Yahoo's ``{CCY}USD=X`` series (or the inverse of
``USD{CCY}=X``) and are forward-filled onto the price calendar.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from optimizacion_portafolios.data.cache import TtlCache, ttl_cache
from optimizacion_portafolios.data.market_data import download_prices, fetch_fundamental_information
from optimizacion_portafolios.data.tiingo_prices import PriceSource, price_source

BASE_CURRENCY = "USD"
_SUBUNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}
# A listing's currency practically never changes; a profile without currency
# (often a failed lookup that defaults to USD) is retried after a minute.
_CURRENCY_TTL_SECONDS = 12 * 60 * 60
_UNKNOWN_CURRENCY_TTL_SECONDS = 60
_RATE_TTL_SECONDS = 15 * 60
_currency_cache = TtlCache(_CURRENCY_TTL_SECONDS, 512)


def ticker_currency(ticker: str) -> tuple[str, float]:
    """``(currency, factor)``: multiply prices by ``factor`` to get ``currency``."""
    if price_source(ticker) is not PriceSource.YFINANCE or ticker.upper().endswith("-USD"):
        return BASE_CURRENCY, 1.0
    cached = _currency_cache.get(ticker)
    if cached is not None:
        return cached
    reported = fetch_fundamental_information(ticker).get("currency")
    currency = str(reported or BASE_CURRENCY).strip()
    result = _SUBUNITS[currency] if currency in _SUBUNITS else (currency.upper(), 1.0)
    _currency_cache.put(ticker, result, None if reported else _UNKNOWN_CURRENCY_TTL_SECONDS)
    return result


ticker_currency.cache_clear = _currency_cache.clear


@ttl_cache(_RATE_TTL_SECONDS, 64, copy=pd.Series.copy)
def usd_rate(currency: str, start: date, end: date) -> pd.Series:
    """USD per unit of ``currency`` per day (raises ``ValueError`` without data).

    Cached 15 minutes per ``(currency, start, end)``; failures are not cached
    (the empty downloads behind them are, briefly, by ``download_prices``).
    """
    direct = f"{currency}USD=X"
    prices = download_prices((direct,), start, end)
    if direct in prices and prices[direct].notna().any():
        return prices[direct].dropna()
    inverse = f"USD{currency}=X"
    prices = download_prices((inverse,), start, end)
    if inverse in prices and prices[inverse].notna().any():
        return 1 / prices[inverse].dropna()
    raise ValueError(f"No se encontró el tipo de cambio de {currency} a USD.")


def prices_in_usd(prices: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Copy of ``prices`` (one column per ticker) with every column in USD."""
    converted = prices.copy()
    rates: dict[str, pd.Series] = {}
    for ticker in prices.columns:
        currency, factor = ticker_currency(str(ticker))
        if currency == BASE_CURRENCY:
            if factor != 1.0:
                converted[ticker] = converted[ticker] * factor
            continue
        if currency not in rates:
            rates[currency] = usd_rate(currency, start, end)
        rate = rates[currency].reindex(prices.index.union(rates[currency].index)).ffill().bfill()
        converted[ticker] = converted[ticker] * factor * rate.reindex(prices.index)
    return converted
