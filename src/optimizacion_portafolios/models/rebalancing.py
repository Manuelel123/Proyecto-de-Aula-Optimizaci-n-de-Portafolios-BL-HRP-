"""Changes to a tracked portfolio: new target weights and the trades they need.

Pure functions (no Flask, no database). Weights are fractions that sum to 1.
A change sells and buys at the closes of its effective date; the commission
(``commission_bps`` of each traded amount) is paid from the portfolio, so the
value after the change is the value before minus the cost and the tracked
value series stays continuous, net of costs.

Turnover is one-way: half the sum of the absolute traded amounts divided by
the value before the change (selling 10 % to buy another 10 % is 10 %).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from optimizacion_portafolios.models.hrp import WeightBounds

BPS = 10_000
_TOLERANCE = 1e-10
_MAX_ITERATIONS = 100


def normalized(weights: dict[str, float] | pd.Series) -> pd.Series:
    series = pd.Series(weights, dtype=float)
    total = series.sum()
    if total <= 0:
        raise ValueError("Los pesos deben sumar un valor positivo.")
    return series / total


def apply_bounds(weights: dict[str, float] | pd.Series, bounds: WeightBounds | None) -> pd.Series:
    """Closest long-only weights within ``bounds`` that sum to 1.

    Weights outside their limits are clipped and the excess (or shortfall) is
    spread over the assets still inside their limits, in proportion to their
    weights, until every limit holds. Raises ``ValueError`` when the limits
    are infeasible.
    """
    weights = normalized(weights)
    if bounds is None:
        return weights
    if (weights < -_TOLERANCE).any():
        raise ValueError("Los límites de peso solo se aplican a portafolios sin posiciones cortas.")
    assets = list(weights.index)
    minimums, maximums = bounds.restricted_to(assets).resolve(assets)
    for _ in range(_MAX_ITERATIONS):
        clipped = weights.clip(lower=minimums, upper=maximums)
        residual = 1.0 - clipped.sum()
        if abs(residual) < _TOLERANCE:
            return clipped
        room = (maximums - clipped) if residual > 0 else (clipped - minimums)
        free = room > _TOLERANCE
        if not free.any():
            raise ValueError("No existe una asignación que cumpla los límites de peso.")
        base = clipped[free].where(clipped[free] > 0, 0.0)
        share = base / base.sum() if base.sum() > 0 else pd.Series(1.0 / free.sum(), index=base.index)
        weights = clipped.copy()
        weights[free] = clipped[free] + residual * share
    raise ValueError("No se pudo ajustar la asignación a los límites de peso.")


def without_assets(weights: dict[str, float], removed: list[str]) -> pd.Series:
    """Drop ``removed`` and spread their weight proportionally over the rest."""
    remaining = {ticker: weight for ticker, weight in weights.items() if ticker not in removed}
    if not remaining:
        raise ValueError("El portafolio debe conservar al menos un activo.")
    return normalized(remaining)


def with_assets(weights: dict[str, float], added: dict[str, float]) -> pd.Series:
    """Add assets at fixed weights, shrinking the others proportionally."""
    total_added = sum(added.values())
    if any(weight <= 0 for weight in added.values()) or total_added >= 1:
        raise ValueError("El peso de los activos nuevos debe ser positivo y sumar menos de 100 %.")
    existing = normalized({t: w for t, w in weights.items() if t not in added}) * (1 - total_added)
    return pd.concat([existing, pd.Series(added, dtype=float)]).groupby(level=0).sum()


@dataclass(frozen=True)
class Trade:
    ticker: str
    side: str  # "buy" | "sell"
    units: float
    price: float
    amount: float
    cost: float


@dataclass(frozen=True)
class ChangePlan:
    value_before: float
    value_after: float
    cost: float
    turnover: float
    weights_before: dict[str, float]
    weights_after: dict[str, float]
    units_after: dict[str, float]
    prices: dict[str, float]
    trades: list[Trade]


def plan_change(
    units: dict[str, float],
    prices: dict[str, float],
    target: dict[str, float] | pd.Series,
    commission_bps: float = 0.0,
) -> ChangePlan:
    """Trades that turn ``units`` into ``target`` weights at ``prices``.

    The cost solves ``cost = c * Σ |w_i (V - cost) - a_i|`` (fixed point),
    where ``a_i`` is the current amount of asset ``i`` and ``V`` the value.
    """
    target = normalized(target)
    tickers = sorted(set(units) | set(target.index))
    missing = [ticker for ticker in tickers if not prices.get(ticker) or prices[ticker] <= 0]
    if missing:
        raise ValueError("No hay precio para: " + ", ".join(missing))
    amounts = {ticker: units.get(ticker, 0.0) * prices[ticker] for ticker in tickers}
    value = sum(amounts.values())
    if value <= 0:
        raise ValueError("El portafolio no tiene valor para rebalancear.")
    rate = commission_bps / BPS
    cost = 0.0
    for _ in range(_MAX_ITERATIONS):
        traded = sum(abs(target.get(ticker, 0.0) * (value - cost) - amounts[ticker]) for ticker in tickers)
        updated = rate * traded
        if abs(updated - cost) < 1e-9:
            cost = updated
            break
        cost = updated
    value_after = value - cost
    trades, new_units = [], {}
    traded_total = 0.0
    for ticker in tickers:
        target_amount = target.get(ticker, 0.0) * value_after
        delta = target_amount - amounts[ticker]
        if target.get(ticker, 0.0) != 0:
            new_units[ticker] = target_amount / prices[ticker]
        if abs(delta) > 1e-9:
            traded_total += abs(delta)
            trades.append(
                Trade(ticker, "buy" if delta > 0 else "sell", abs(delta) / prices[ticker], prices[ticker], abs(delta), rate * abs(delta))
            )
    return ChangePlan(
        value_before=value,
        value_after=value_after,
        cost=cost,
        turnover=traded_total / (2 * value),
        weights_before={ticker: amounts[ticker] / value for ticker in tickers if amounts[ticker]},
        weights_after={ticker: float(weight) for ticker, weight in target.items() if weight},
        units_after=new_units,
        prices={ticker: float(prices[ticker]) for ticker in tickers},
        trades=trades,
    )
