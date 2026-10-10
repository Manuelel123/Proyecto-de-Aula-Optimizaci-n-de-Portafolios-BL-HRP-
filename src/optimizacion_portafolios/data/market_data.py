"""Market data access: Tiingo prices with Yahoo Finance fallback and profiles."""

from collections import OrderedDict
from datetime import date, timedelta
from functools import lru_cache
import logging
from math import isfinite
from threading import Lock
from time import monotonic, sleep

import pandas as pd
import requests
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from optimizacion_portafolios.data.tiingo_prices import (
    PriceSource,
    download_tiingo_crypto_prices,
    download_tiingo_prices,
    normalize_dates,
    price_source,
    tiingo_api_key,
)

logger = logging.getLogger(__name__)
_INFO_CACHE_TTL_SECONDS = 15 * 60
_INFO_CACHE_MAX_SIZE = 512
_PRICE_CACHE_TTL_SECONDS = 15 * 60
_PRICE_CACHE_MAX_SIZE = 64
_RATE_LIMIT_RETRIES = 2
_info_lock = Lock()
_price_lock = Lock()
_price_cache: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()


@lru_cache(maxsize=_INFO_CACHE_MAX_SIZE)
def _cached_ticker_info(ticker: str, cache_window: int) -> dict:
    # Use yfinance's own curl_cffi session (it applies its own timeouts). A plain
    # requests.Session disables browser impersonation and Yahoo then throttles or
    # stalls profile lookups.
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return yf.Ticker(ticker).get_info()
        except YFRateLimitError:
            if attempt == _RATE_LIMIT_RETRIES:
                raise
            delay = 2**attempt
            logger.warning(
                "Yahoo Finance rate-limited profile lookup for %s; retrying in %s seconds",
                ticker,
                delay,
            )
            sleep(delay)
    raise RuntimeError("Yahoo Finance retry loop ended unexpectedly.")


def _ticker_info(ticker: str) -> dict:
    cache_window = int(monotonic() // _INFO_CACHE_TTL_SECONDS)
    with _info_lock:
        return dict(_cached_ticker_info(ticker, cache_window))


def _download_yfinance_prices(
    tickers: tuple[str, ...], start: date, end: date
) -> pd.DataFrame:
    """Download adjusted closing prices from Yahoo Finance."""
    if not tickers:
        return pd.DataFrame()
    downloaded = yf.download(
        list(tickers),
        start=start,
        end=end + timedelta(days=1),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=20,
    )
    if downloaded.empty:
        return pd.DataFrame()

    if isinstance(downloaded.columns, pd.MultiIndex):
        close_level = next(
            (
                level
                for level in range(downloaded.columns.nlevels)
                if "Close" in downloaded.columns.get_level_values(level)
            ),
            None,
        )
        if close_level is None:
            return pd.DataFrame()
        prices = downloaded.xs("Close", axis=1, level=close_level)
    elif "Close" in downloaded.columns:
        prices = downloaded["Close"]
    else:
        return pd.DataFrame()

    if isinstance(prices, pd.Series):
        prices = prices.to_frame(name=tickers[0])
    if not isinstance(prices, pd.DataFrame):
        return pd.DataFrame()
    available = [ticker for ticker in tickers if ticker in prices.columns]
    return prices.loc[:, available]


def _download_from_tiingo(
    tickers: tuple[str, ...], start: date, end: date, api_key: str
) -> dict[str, pd.Series]:
    """Download the tickers Tiingo covers; the rest are left out of the result."""
    stocks = tuple(t for t in tickers if price_source(t) is PriceSource.TIINGO)
    cryptos = tuple(
        t for t in tickers if price_source(t) is PriceSource.TIINGO_CRYPTO
    )
    prices = download_tiingo_prices(stocks, start, end, api_key)
    prices.update(download_tiingo_crypto_prices(cryptos, start, end, api_key))
    return prices


def download_prices(
    tickers: tuple[str, ...], start: date, end: date
) -> pd.DataFrame:
    """Download adjusted closing prices with a consistent ticker-column shape.

    Tiingo is used when ``TIINGO_API_KEY`` is set; tickers it does not cover
    (or for which it returns no data) are downloaded from Yahoo Finance.

    Results are cached for up to 15 minutes per ``(tickers, start, end)`` (same
    ``cache_window`` scheme as the profile cache) and every call returns an
    independent copy, so callers may mutate the frame freely. Empty results are
    not cached, so a transient provider failure is retried on the next call.
    """
    tickers = tuple(tickers)
    if not tickers:
        return pd.DataFrame()
    cache_window = int(monotonic() // _PRICE_CACHE_TTL_SECONDS)
    key = (tickers, start, end, cache_window)
    with _price_lock:
        cached = _price_cache.get(key)
        if cached is not None:
            _price_cache.move_to_end(key)
            return cached.copy()
    # Download outside the lock so slow requests do not serialize other users.
    prices = _download_prices_uncached(tickers, start, end)
    if not prices.empty:
        with _price_lock:
            _price_cache[key] = prices.copy()
            _price_cache.move_to_end(key)
            stale = [k for k in _price_cache if k[-1] != cache_window]
            for old_key in stale:
                del _price_cache[old_key]
            while len(_price_cache) > _PRICE_CACHE_MAX_SIZE:
                _price_cache.popitem(last=False)
    return prices


def clear_price_cache() -> None:
    """Drop every cached price download (used by tests)."""
    with _price_lock:
        _price_cache.clear()


def _download_prices_uncached(
    tickers: tuple[str, ...], start: date, end: date
) -> pd.DataFrame:
    """Download prices from Tiingo/Yahoo without caching."""
    if not tickers:
        return pd.DataFrame()
    api_key = tiingo_api_key()
    series = {}
    if api_key:
        series = _download_from_tiingo(tickers, start, end, api_key)

    pending = tuple(ticker for ticker in tickers if ticker not in series)
    yahoo_prices = _download_yfinance_prices(pending, start, end)
    for ticker in yahoo_prices.columns:
        series.setdefault(ticker, yahoo_prices[ticker])

    frames = []
    for ticker in tickers:
        if ticker not in series:
            continue
        values = series[ticker].copy()
        values.index = normalize_dates(values.index)
        values = values[~values.index.duplicated(keep="last")]
        frames.append(values.rename(ticker))
    if not frames:
        return pd.DataFrame()
    prices = pd.concat(frames, axis=1, sort=True)
    window = (prices.index >= pd.Timestamp(start)) & (
        prices.index <= pd.Timestamp(end)
    )
    return prices.loc[window].sort_index().rename_axis(index="Fecha")


def fetch_fundamental_information(ticker: str) -> dict:
    """Return the Yahoo Finance profile for a single ticker."""
    return _ticker_info(ticker)


def _exchange_rate_to_usd(currency: str) -> float:
    currency = currency.upper()
    if currency == "USD":
        return 1.0
    last_error = None
    for symbol, invert in ((f"{currency}USD=X", False), (f"USD{currency}=X", True)):
        try:
            history = yf.Ticker(symbol).history(period="5d")
        except (requests.RequestException, ValueError) as error:
            last_error = error
            continue
        if history.empty or "Close" not in history:
            continue
        closes = history["Close"].dropna()
        if closes.empty:
            continue
        rate = float(closes.iloc[-1])
        if not isfinite(rate) or rate <= 0:
            continue
        return 1 / rate if invert else rate
    raise ValueError(
        f"No se pudo obtener el tipo de cambio de {currency} a USD. "
        f"{last_error or ''}"
    )


def fetch_market_cap_usd(ticker: str) -> float | None:
    """Get market capitalization or ETF net assets, converted into USD."""
    info = _ticker_info(ticker)
    amount = info.get("marketCap")
    if amount is None and info.get("quoteType") in {"ETF", "MUTUALFUND"}:
        amount = info.get("totalAssets")
    if amount is None:
        return None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    if not isfinite(amount) or amount <= 0:
        return None

    currency = str(info.get("currency") or "").strip()
    if not currency:
        return None
    if currency in {"GBp", "GBX"}:
        amount /= 100
        currency = "GBP"
    return amount * _exchange_rate_to_usd(currency)
