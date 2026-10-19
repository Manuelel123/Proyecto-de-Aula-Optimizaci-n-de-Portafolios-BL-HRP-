"""Tracking of a fixed ("optimal") portfolio: pure functions over pandas.

The portfolio is held as units ("buy and hold") between changes. Each change
(rebalance, removal, addition) starts a new segment with new units bought with
the portfolio value of that day minus the trading cost, so the value series is
continuous across segments and already net of costs. Prices are adjusted
closes, so dividends are implicitly reinvested (total return). A ticker that
stops trading keeps its last price (forward fill).

Nothing here touches Flask or the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

BPS = 10_000


@dataclass(frozen=True)
class Segment:
    """Units held from ``start`` (inclusive) until the next segment starts."""

    start: pd.Timestamp
    units: dict[str, float]
    label: str = ""


@dataclass(frozen=True)
class Positions:
    units: dict[str, float]
    prices: dict[str, float]
    invested: float
    cost: float
    amounts: dict[str, float] = field(default_factory=dict)


def initial_positions(
    capital: float, weights: dict[str, float], prices: dict[str, float], commission_bps: float = 0.0
) -> Positions:
    """Units bought with ``capital`` at ``prices``; the commission is paid first.

    The cost is ``commission_bps`` of the capital, and the rest
    (``invested``) is split by ``weights`` (normalized to sum 1).
    """
    if capital <= 0:
        raise ValueError("El capital inicial debe ser positivo.")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("Los pesos deben sumar un valor positivo.")
    missing = [ticker for ticker in weights if not prices.get(ticker) or prices[ticker] <= 0]
    if missing:
        raise ValueError("No hay precio de cierre para: " + ", ".join(missing))
    cost = capital * commission_bps / BPS
    invested = capital - cost
    amounts = {ticker: invested * weight / total for ticker, weight in weights.items()}
    units = {ticker: amounts[ticker] / prices[ticker] for ticker in weights}
    return Positions(units, {t: float(prices[t]) for t in weights}, invested, cost, amounts)


def last_prices(prices: pd.DataFrame, on: pd.Timestamp | None = None) -> tuple[pd.Timestamp, dict[str, float]]:
    """Latest close of each column on or before ``on`` (forward-filled)."""
    window = prices.sort_index().ffill()
    if on is not None:
        window = window.loc[: pd.Timestamp(on)]
    if window.empty:
        raise ValueError("No hay precios disponibles para esa fecha.")
    row = window.iloc[-1]
    return window.index[-1], {str(ticker): float(value) for ticker, value in row.dropna().items()}


def holding_values(prices: pd.DataFrame, segments: list[Segment]) -> pd.DataFrame:
    """Value of each position per day (columns = every ticker ever held).

    Rows start on the first segment's start. Raises ``ValueError`` when a held
    ticker has no price column.
    """
    if not segments:
        raise ValueError("Se requiere al menos un tramo de posiciones.")
    ordered = sorted(segments, key=lambda segment: segment.start)
    tickers = sorted({ticker for segment in ordered for ticker in segment.units})
    missing = [ticker for ticker in tickers if ticker not in prices.columns]
    if missing:
        raise ValueError("Faltan precios de: " + ", ".join(missing))
    prices = prices.sort_index().ffill()
    prices = prices.loc[prices.index >= ordered[0].start, tickers]
    units = pd.DataFrame(0.0, index=prices.index, columns=tickers)
    for position, segment in enumerate(ordered):
        rows = units.index >= segment.start
        if position + 1 < len(ordered):
            rows &= units.index < ordered[position + 1].start
        units.loc[rows, list(segment.units)] = list(segment.units.values())
    return (prices * units).fillna(0.0)


def portfolio_values(prices: pd.DataFrame, segments: list[Segment]) -> pd.Series:
    """Daily portfolio value: sum of units × close of the segment in force."""
    return holding_values(prices, segments).sum(axis=1).rename("Portafolio")


def current_weights(values: pd.DataFrame) -> pd.Series:
    """Weights of the last day of ``holding_values`` (zero positions dropped)."""
    last = values.iloc[-1]
    last = last[last.abs() > 0]
    return (last / last.sum()).rename("Peso actual")


def weight_drift(current: pd.Series, target: dict[str, float]) -> pd.Series:
    """Current minus target weight per ticker (positive = overweight)."""
    tickers = sorted(set(current.index) | set(target))
    current = current.reindex(tickers).fillna(0.0)
    target_series = pd.Series(target, dtype=float).reindex(tickers).fillna(0.0)
    return (current - target_series).rename("Desviación")


def rebased(series: pd.Series, start_value: float, start: pd.Timestamp | None = None) -> pd.Series:
    """``series`` scaled so its first value (on or after ``start``) is ``start_value``."""
    values = series.sort_index().ffill().dropna()
    if start is not None:
        values = values.loc[values.index >= pd.Timestamp(start)]
    if values.empty:
        return values
    return values / values.iloc[0] * start_value


def tracking_summary(values: pd.Series, benchmark: pd.Series, initial_capital: float) -> dict:
    """Headline numbers of the dashboard (value, return, excess over benchmark)."""
    values = values.dropna()
    current = float(values.iloc[-1])
    total_return = current / initial_capital - 1
    benchmark = rebased(benchmark, initial_capital, values.index[0])
    benchmark_return = (
        float(benchmark.reindex(values.index).ffill().iloc[-1]) / initial_capital - 1
        if not benchmark.empty
        else None
    )
    return {
        "value": current,
        "total_return": total_return,
        "benchmark_return": benchmark_return,
        "excess_return": total_return - benchmark_return if benchmark_return is not None else None,
        "start": values.index[0],
        "end": values.index[-1],
        "days": len(values),
    }
