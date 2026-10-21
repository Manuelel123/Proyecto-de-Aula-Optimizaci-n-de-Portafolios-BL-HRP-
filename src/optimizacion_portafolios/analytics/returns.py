"""Price preparation and daily returns shared by the optimization pages (no Flask)."""

from __future__ import annotations

import pandas as pd


def prepare_prices(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(raw, aligned)`` of a ``download_prices`` frame.

    ``raw`` drops the tickers without any price and keeps each asset's own
    calendar (for the price explorer and per-asset returns); ``aligned`` is
    forward-filled and keeps only the days where every asset has a price
    (the input of the models).
    """
    raw = raw.dropna(axis="columns", how="all")
    return raw, raw.ffill().dropna()


def asset_daily_returns(prices: pd.Series) -> pd.Series:
    """Daily simple returns of one asset over its own quotes (no ffill)."""
    return prices.dropna().pct_change(fill_method=None).dropna()


def weighted_portfolio_returns(prices: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Daily returns of a portfolio rebalanced daily to ``weights`` on aligned prices."""
    aligned = prices.ffill().dropna()
    returns = aligned.pct_change(fill_method=None).dropna()
    return returns.loc[:, list(weights)].dot(pd.Series(weights))


def returns_matrix_series(
    raw_prices: pd.DataFrame,
    assets,
    *,
    portfolio: pd.Series | None = None,
    portfolio_key: str = "portfolio",
    benchmark: str | None = None,
    benchmark_prices: pd.Series | None = None,
) -> dict[str, pd.Series]:
    """Series of the returns matrix in display order: portfolio, benchmark, assets.

    The portfolio is skipped when ``None`` or empty; the benchmark when it is
    one of ``assets``. Each asset (and the benchmark) uses its own price
    history, so different start dates and crypto weekends are kept.
    """
    series: dict[str, pd.Series] = {}
    if portfolio is not None and not portfolio.empty:
        series[portfolio_key] = portfolio
    assets = list(assets)
    if benchmark is not None and benchmark not in assets:
        series[benchmark] = asset_daily_returns(benchmark_prices)
    for ticker in assets:
        series[ticker] = asset_daily_returns(raw_prices[ticker])
    return series
