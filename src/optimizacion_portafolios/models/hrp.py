"""Hierarchical Risk Parity optimization."""

import numpy as np
import pandas as pd
from pypfopt import HRPOpt
from scipy.cluster.hierarchy import to_tree

from optimizacion_portafolios.analytics.statistics import TRADING_DAYS


def calculate_hrp(returns: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Return HRP weights and the correlation matrix in quasi-diagonal order."""
    optimizer = HRPOpt(returns=returns)
    weights = pd.Series(optimizer.optimize(linkage_method="single"), dtype=float)
    correlation = returns.corr()
    order = returns.columns[to_tree(optimizer.clusters).pre_order()].tolist()
    return weights.sort_values(ascending=False), correlation.loc[order, order]


def calculate_hrp_contributions(
    returns: pd.DataFrame, weights: pd.Series
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_contributions = returns.mul(weights, axis="columns")
    summary = pd.DataFrame(index=weights.index)
    summary["Peso HRP"] = weights
    summary["Retorno anualizado"] = returns.mean() * TRADING_DAYS
    summary["Volatilidad anualizada"] = returns.std() * np.sqrt(TRADING_DAYS)
    summary["Contribución anualizada"] = daily_contributions.mean() * TRADING_DAYS
    total_return = summary["Contribución anualizada"].sum()
    summary["Participación del retorno"] = (
        summary["Contribución anualizada"] / total_return
        if total_return != 0
        else 0
    )
    return daily_contributions, summary
