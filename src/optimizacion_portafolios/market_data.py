"""Yahoo Finance data access for portfolio analysis."""

from datetime import date, timedelta
from math import isfinite

import pandas as pd
import requests
import yfinance as yf


class TimeoutSession(requests.Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", 20)
        return super().request(*args, **kwargs)


def download_prices(
    tickers: tuple[str, ...], start: date, end: date
) -> pd.DataFrame:
    """Download adjusted closing prices with a consistent ticker-column shape."""
    if not tickers:
        return pd.DataFrame()
    downloaded = yf.download(
        list(tickers),
        start=start,
        end=end + timedelta(days=1),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=True,
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
    prices = prices.rename_axis(index="Fecha")
    available = [ticker for ticker in tickers if ticker in prices.columns]
    return prices.loc[:, available].sort_index()


def fetch_fundamental_information(ticker: str) -> dict:
    """Return the Yahoo Finance profile for a single ticker."""
    return yf.Ticker(ticker, session=TimeoutSession()).get_info()


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
    info = yf.Ticker(ticker, session=TimeoutSession()).get_info()
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
