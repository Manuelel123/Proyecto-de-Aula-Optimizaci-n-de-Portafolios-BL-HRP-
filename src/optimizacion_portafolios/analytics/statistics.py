"""Descriptive return and risk statistics for price series."""

from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252
EMA_SPAN = 500


def date_years_ago(value: date, years: int = 1) -> date:
    """Return the same calendar day ``years`` earlier (Feb 29 falls back to Feb 28)."""
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def calculate_statistics(prices: pd.DataFrame) -> pd.DataFrame:
    returns = prices.pct_change(fill_method=None).dropna(how="all")
    statistics = pd.DataFrame(index=prices.columns)
    statistics["Último precio"] = prices.ffill().iloc[-1]
    statistics["Retorno total"] = (prices.ffill().iloc[-1] / prices.bfill().iloc[0]) - 1
    statistics["Retorno anualizado"] = (
        (1 + statistics["Retorno total"]) ** (TRADING_DAYS / max(len(returns), 1)) - 1
    )
    statistics["Volatilidad anualizada"] = returns.std() * np.sqrt(TRADING_DAYS)
    statistics["Máxima caída"] = (
        prices.ffill() / prices.ffill().cummax() - 1
    ).min()
    return statistics


def calculate_expected_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Annualized compounded historical and exponentially weighted mean returns.

    Matches PyPortfolioOpt's ``mean_historical_return`` (geometric mean, i.e.
    CAGR over the observed returns) and ``ema_historical_return`` (EMA of daily
    returns with ``span=500`` compounded over ``TRADING_DAYS``).
    """
    prices = prices.dropna(axis="columns", how="all")
    returns = prices.pct_change(fill_method=None).dropna(how="all")
    historical = (1 + returns).prod() ** (TRADING_DAYS / returns.count()) - 1
    ema = (1 + returns.ewm(span=EMA_SPAN).mean().iloc[-1]) ** TRADING_DAYS - 1
    return pd.DataFrame(
        {
            "Retorno histórico esperado": historical,
            "Retorno esperado EMA": ema,
        }
    )
