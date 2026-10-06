"""Reusable portfolio analytics, independent of the web presentation layer."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import quantstats as qs
from pypfopt import HRPOpt, expected_returns
from scipy.cluster.hierarchy import to_tree

from optimizacion_portafolios.catalogs import PERIODOS_VOLATILIDAD


def date_one_year_ago(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def calculate_statistics(prices: pd.DataFrame) -> pd.DataFrame:
    returns = prices.pct_change(fill_method=None).dropna(how="all")
    statistics = pd.DataFrame(index=prices.columns)
    statistics["Último precio"] = prices.ffill().iloc[-1]
    statistics["Retorno total"] = (prices.ffill().iloc[-1] / prices.bfill().iloc[0]) - 1
    statistics["Retorno anualizado"] = (
        (1 + statistics["Retorno total"]) ** (252 / max(len(returns), 1)) - 1
    )
    statistics["Volatilidad anualizada"] = returns.std() * np.sqrt(252)
    statistics["Máxima caída"] = (
        prices.ffill() / prices.ffill().cummax() - 1
    ).min()
    return statistics


def calculate_expected_returns(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.dropna(axis="columns", how="all")
    historical = expected_returns.mean_historical_return(
        prices, returns_data=False, compounding=True, frequency=252
    )
    ema = expected_returns.ema_historical_return(
        prices,
        returns_data=False,
        compounding=True,
        span=500,
        frequency=252,
    )
    return pd.DataFrame(
        {
            "Retorno histórico esperado": historical,
            "Retorno esperado EMA": ema,
        }
    )


def calculate_hrp(returns: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    optimizer = HRPOpt(returns=returns)
    weights = pd.Series(optimizer.optimize(linkage_method="single"), dtype=float)
    correlation = returns.corr()
    order = returns.columns[to_tree(optimizer.clusters).pre_order()].tolist()
    return weights.sort_values(ascending=False), correlation.loc[order, order]


def calculate_monthly_volatility(returns: pd.DataFrame) -> pd.DataFrame:
    volatility = returns.resample("ME").std() * np.sqrt(21)
    return volatility.dropna(how="all")


def calculate_historical_volatility(
    returns: pd.DataFrame,
    period: str,
    start: date | pd.Timestamp,
    end: date | pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    annualized_volatility = (daily_std * np.sqrt(252)).where(
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


def calculate_quantstats_metrics(returns: pd.Series) -> pd.DataFrame:
    metrics = {
        "Retorno anual compuesto": qs.stats.cagr(returns),
        "Volatilidad anualizada": qs.stats.volatility(returns),
        "Ratio de Sharpe": qs.stats.sharpe(returns),
        "Ratio de Sortino": qs.stats.sortino(returns),
        "Máxima caída": qs.stats.max_drawdown(returns),
        "Ratio de Calmar": qs.stats.calmar(returns),
        "Porcentaje de días positivos": qs.stats.win_rate(returns),
    }
    return pd.DataFrame.from_dict(metrics, orient="index", columns=["Valor"])


def generate_tearsheet(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    title: str,
    filename: str,
) -> bytes:
    with TemporaryDirectory() as directory:
        report_path = Path(directory) / "report.html"
        qs.reports.html(
            portfolio_returns,
            benchmark=benchmark_returns,
            output=str(report_path),
            title=f"Strategy Tearsheet - {title}",
            download_filename=filename,
        )
        return report_path.read_bytes()


def calculate_hrp_contributions(
    returns: pd.DataFrame, weights: pd.Series
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_contributions = returns.mul(weights, axis="columns")
    summary = pd.DataFrame(index=weights.index)
    summary["Peso HRP"] = weights
    summary["Retorno anualizado"] = returns.mean() * 252
    summary["Volatilidad anualizada"] = returns.std() * np.sqrt(252)
    summary["Contribución anualizada"] = daily_contributions.mean() * 252
    total_return = summary["Contribución anualizada"].sum()
    summary["Participación del retorno"] = (
        summary["Contribución anualizada"] / total_return
        if total_return != 0
        else 0
    )
    return daily_contributions, summary
