"""Realized volatility over calendar periods."""

from datetime import date

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics.statistics import TRADING_DAYS
from optimizacion_portafolios.data.catalogs import PERIODOS_VOLATILIDAD


def calculate_monthly_volatility(returns: pd.DataFrame) -> pd.DataFrame:
    volatility = returns.resample("ME").std() * np.sqrt(21)
    return volatility.dropna(how="all")


def calculate_historical_volatility(
    returns: pd.DataFrame,
    period: str,
    start: date | pd.Timestamp,
    end: date | pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return per-period and annualized volatility, keeping only complete periods."""
    rule = PERIODOS_VOLATILIDAD[period]
    start_timestamp = pd.Timestamp(start).normalize()
    end_timestamp = pd.Timestamp(end).normalize()
    if rule in {"15D", "45D"}:
        days = int(rule.removesuffix("D"))
        group_ids = (returns.index.normalize() - start_timestamp).days // days
        groups = returns.groupby(group_ids)
        daily_std = groups.std(ddof=1)
        observations = groups.count()
        labels = start_timestamp + pd.to_timedelta(daily_std.index * days, unit="D")
        daily_std.index = labels
        observations.index = labels
    else:
        groups = returns.resample(rule)
        daily_std = groups.std(ddof=1)
        observations = groups.count()

    period_volatility = (daily_std * np.sqrt(observations)).where(
        observations >= 2
    ).dropna(how="all")
    annualized_volatility = (daily_std * np.sqrt(TRADING_DAYS)).where(
        observations >= 2
    ).dropna(how="all")
    periods = annualized_volatility.index

    if rule == "W-FRI":
        period_start = periods - pd.Timedelta(days=6)
        complete = (period_start >= start_timestamp) & (periods <= end_timestamp)
    elif rule in {"15D", "45D"}:
        days = int(rule.removesuffix("D"))
        period_end = periods + pd.Timedelta(days=days - 1)
        complete = (periods >= start_timestamp) & (period_end <= end_timestamp)
    elif rule == "ME":
        period_start = periods.to_period("M").to_timestamp(how="start")
        complete = (period_start >= start_timestamp) & (periods <= end_timestamp)
    else:
        period_start = (periods.to_period("M") - 1).to_timestamp(how="start")
        complete = (period_start >= start_timestamp) & (periods <= end_timestamp)

    return period_volatility.loc[complete], annualized_volatility.loc[complete]
