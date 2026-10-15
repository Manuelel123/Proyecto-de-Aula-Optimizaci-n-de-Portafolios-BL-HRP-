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

from optimizacion_portafolios.data import price_store
from optimizacion_portafolios.data.tiingo_prices import (
    PriceSource,
    download_tiingo_crypto_ohlcv,
    download_tiingo_crypto_prices,
    download_tiingo_ohlcv,
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
_ohlcv_cache: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
_YAHOO_OHLCV_FIELDS = dict(zip(OHLCV_COLUMNS, ("Open", "High", "Low", "Close", "Volume")))


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
    (or for which it returns no data) are downloaded from Yahoo Finance. When
    the InsForge price store is configured, tickers with fresh stored coverage
    are read from it and new downloads are written back (``price_store``).

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
    prices = _download_prices_persistent(tickers, start, end)
    if not prices.empty:
        with _price_lock:
            _store(_price_cache, key, prices)
    return prices


def clear_price_cache() -> None:
    """Drop every cached price download (used by tests)."""
    with _price_lock:
        _price_cache.clear()
        _ohlcv_cache.clear()


def _store(cache: "OrderedDict[tuple, pd.DataFrame]", key: tuple, frame: pd.DataFrame) -> None:
    """Insert ``frame`` in ``cache`` dropping entries of older windows (lock held)."""
    cache[key] = frame.copy()
    cache.move_to_end(key)
    for old_key in [k for k in cache if k[-1] != key[-1]]:
        del cache[old_key]
    while len(cache) > _PRICE_CACHE_MAX_SIZE:
        cache.popitem(last=False)


def _yahoo_field(downloaded: pd.DataFrame, field: str, ticker: str) -> pd.Series | None:
    """Column ``field`` of a single-ticker ``yf.download`` frame (flat or MultiIndex)."""
    if isinstance(downloaded.columns, pd.MultiIndex):
        level = next(
            (
                level
                for level in range(downloaded.columns.nlevels)
                if field in downloaded.columns.get_level_values(level)
            ),
            None,
        )
        if level is None:
            return None
        values = downloaded.xs(field, axis=1, level=level)
        if isinstance(values, pd.DataFrame):
            if ticker in values.columns:
                values = values[ticker]
            elif values.shape[1] == 1:
                values = values.iloc[:, 0]
            else:
                return None
        return values
    return downloaded[field] if field in downloaded.columns else None


def _download_yfinance_ohlcv(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Adjusted daily OHLCV of one ticker from Yahoo Finance (``auto_adjust``)."""
    downloaded = yf.download(
        [ticker],
        start=start,
        end=end + timedelta(days=1),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=20,
    )
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()
    columns = {}
    for column, field in _YAHOO_OHLCV_FIELDS.items():
        values = _yahoo_field(downloaded, field, ticker)
        columns[column] = (
            pd.to_numeric(values, errors="coerce").astype(float)
            if values is not None
            else pd.Series(float("nan"), index=downloaded.index)
        )
    frame = pd.DataFrame(columns, index=downloaded.index)
    return frame.loc[frame["close"].notna()]


def download_ohlcv(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Daily OHLCV of one ticker with columns ``open, high, low, close, volume``.

    Same routing as ``download_prices``: Tiingo (adjusted ``adjOpen``..
    ``adjVolume``; raw fields for crypto) when ``TIINGO_API_KEY`` is set and it
    covers the ticker, otherwise Yahoo Finance with ``auto_adjust=True``, so the
    close matches the adjusted close of ``download_prices``. Fields the provider
    does not publish are all-NaN columns (never filled in). The index is
    ``Fecha`` (tz-naive days, ascending, unique). Empty frame without data.
    Cached like ``download_prices``; every call returns an independent copy.
    """
    cache_window = int(monotonic() // _PRICE_CACHE_TTL_SECONDS)
    key = (ticker, start, end, cache_window)
    with _price_lock:
        cached = _ohlcv_cache.get(key)
        if cached is not None:
            _ohlcv_cache.move_to_end(key)
            return cached.copy()
    frame = _download_ohlcv_uncached(ticker, start, end)
    if not frame.empty:
        with _price_lock:
            _store(_ohlcv_cache, key, frame)
    return frame


def _download_ohlcv_uncached(ticker: str, start: date, end: date) -> pd.DataFrame:
    frame = None
    api_key = tiingo_api_key()
    source = price_source(ticker)
    if api_key and source is PriceSource.TIINGO:
        frame = download_tiingo_ohlcv(ticker, start, end, api_key)
    elif api_key and source is PriceSource.TIINGO_CRYPTO:
        frame = download_tiingo_crypto_ohlcv(ticker, start, end, api_key)
    if frame is None or frame.empty:
        frame = _download_yfinance_ohlcv(ticker, start, end)
    if frame.empty:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS))
    frame = frame.reindex(columns=list(OHLCV_COLUMNS)).astype(float)
    frame.index = normalize_dates(frame.index)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    window = (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
    return frame.loc[window].rename_axis(index="Fecha")


def _download_prices_persistent(
    tickers: tuple[str, ...], start: date, end: date
) -> pd.DataFrame:
    """Prices from the InsForge store when fresh, downloading only the rest."""
    stored, pending = price_store.load(tickers, start, end)
    downloaded = _download_prices_uncached(pending, start, end) if pending else pd.DataFrame()
    price_store.save(downloaded, start, end)
    if not stored:
        return downloaded
    return _assemble_prices(
        tickers,
        {**stored, **{ticker: downloaded[ticker] for ticker in downloaded.columns}},
        start,
        end,
    )


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
    return _assemble_prices(tickers, series, start, end)


def _assemble_prices(
    tickers: tuple[str, ...], series: dict[str, pd.Series], start: date, end: date
) -> pd.DataFrame:
    """One column per ticker (request order), normalized days within the window."""
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
