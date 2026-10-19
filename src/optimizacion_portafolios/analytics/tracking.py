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

import math
from dataclasses import dataclass, field

import pandas as pd
import quantstats as qs

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


# =============================================================================
# Tracking metrics (daily series on business days; rf as a yearly fraction)
# =============================================================================

TRADING_DAYS = 252
ROLLING_VOL_WINDOWS = (21, 63)
ROLLING_SHARPE_WINDOW = 126
ROLLING_CORRELATION_WINDOW = 63
VAR_CONFIDENCE = 0.95


def business_days(frame):
    """Rows on Monday-Friday only (crypto weekends would dilute daily risk)."""
    return frame.loc[frame.index.dayofweek < 5]


def daily_returns(values: pd.Series) -> pd.Series:
    return values.astype(float).pct_change(fill_method=None).dropna()


def _enough(returns: pd.Series, minimum: int = 2) -> bool:
    return len(returns.dropna()) >= minimum


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ----------------------------------------------------------------- returns
def cumulative_return(values: pd.Series) -> float | None:
    values = values.dropna()
    return float(values.iloc[-1] / values.iloc[0] - 1) if len(values) >= 2 else None


def period_return(values: pd.Series, start: pd.Timestamp) -> float | None:
    """Return since the last close before ``start`` (or since the first value)."""
    values = values.dropna()
    if values.empty:
        return None
    before = values.loc[values.index < start]
    base = before.iloc[-1] if not before.empty else values.iloc[0]
    return float(values.iloc[-1] / base - 1)


def period_returns(values: pd.Series) -> dict[str, float | None]:
    """Month-to-date, year-to-date and last-year returns.

    MTD and YTD start at the tracking start when it is inside the period; the
    one-year return is ``None`` until a full year of history exists.
    """
    values = values.dropna()
    end = values.index[-1]
    year_ago = end - pd.DateOffset(years=1)
    return {
        "mtd": period_return(values, end.replace(day=1)),
        "ytd": period_return(values, end.replace(month=1, day=1)),
        "one_year": period_return(values, year_ago + pd.Timedelta(days=1)) if values.index[0] <= year_ago else None,
    }


def cagr(values: pd.Series) -> float | None:
    """Compound annual growth over the calendar span of ``values``."""
    values = values.dropna()
    if len(values) < 2:
        return None
    years = (values.index[-1] - values.index[0]).days / 365.25
    if years <= 0:
        return None
    return float((values.iloc[-1] / values.iloc[0]) ** (1 / years) - 1)


def return_contributions(holdings: pd.DataFrame) -> pd.Series:
    """Arithmetic contribution of each asset to the cumulative return.

    Daily profit of each position held on consecutive days with the same
    units, divided by the initial portfolio value. On a change day the units
    switch, so that day's step is the trading cost (attributed to no asset).
    """
    holdings = holdings.astype(float)
    total = holdings.sum(axis=1)
    if len(total) < 2 or total.iloc[0] == 0:
        return pd.Series(dtype=float)
    previous = holdings.shift(1)
    pnl = (holdings - previous).where((previous.abs() > 0) & (holdings.abs() > 0), 0.0)
    return (pnl.iloc[1:].sum() / total.iloc[0]).rename("Contribución al retorno")


# -------------------------------------------------------------------- risk
def annualized_volatility(returns: pd.Series) -> float | None:
    """Annualized volatility (QuantStats)."""
    return _number(qs.stats.volatility(returns, periods=TRADING_DAYS)) if _enough(returns) else None


def rolling_volatility(returns: pd.Series, window: int) -> pd.Series:
    return (returns.rolling(window).std(ddof=1) * TRADING_DAYS**0.5).dropna()


def drawdown_series(values: pd.Series) -> pd.Series:
    values = values.dropna()
    return (values / values.cummax() - 1).rename("Drawdown")


def drawdown_stats(values: pd.Series) -> dict:
    """Current and maximum drawdown, and the longest calendar days below a peak."""
    drawdown = drawdown_series(values)
    if drawdown.empty:
        return {"current": None, "max": None, "max_duration_days": None, "current_duration_days": None}
    longest = current = 0
    peak_day = drawdown.index[0]
    for day, value in drawdown.items():
        if value >= 0:
            peak_day = day
            current = 0
        else:
            current = (day - peak_day).days
            longest = max(longest, current)
    return {
        "current": float(drawdown.iloc[-1]),
        "max": float(drawdown.min()),
        "max_duration_days": int(longest),
        "current_duration_days": int(current),
    }


def historical_var(returns: pd.Series, confidence: float = VAR_CONFIDENCE) -> float | None:
    """Daily historical VaR as a positive loss fraction (empirical quantile)."""
    if not _enough(returns, 20):
        return None
    return float(-returns.quantile(1 - confidence))


def historical_cvar(returns: pd.Series, confidence: float = VAR_CONFIDENCE) -> float | None:
    """Mean daily loss at or beyond the historical VaR (positive fraction)."""
    var = historical_var(returns, confidence)
    if var is None:
        return None
    tail = returns[returns <= -var]
    return float(-tail.mean()) if not tail.empty else var


def semideviation(returns: pd.Series) -> float | None:
    """Annualized downside deviation (target 0)."""
    if not _enough(returns):
        return None
    return float(returns.clip(upper=0).pow(2).mean() ** 0.5 * TRADING_DAYS**0.5)


def risk_contributions(asset_returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Share of portfolio variance of each asset: w_i (Σw)_i / w'Σw."""
    tickers = [ticker for ticker in weights.index if ticker in asset_returns.columns]
    returns = asset_returns[tickers].dropna() if tickers else pd.DataFrame()
    if len(returns) < 3:
        return pd.Series(dtype=float)
    weights = weights.reindex(tickers).fillna(0.0)
    marginal = returns.cov().to_numpy() @ weights.to_numpy()
    variance = float(weights.to_numpy() @ marginal)
    if variance <= 0:
        return pd.Series(dtype=float)
    return pd.Series(weights.to_numpy() * marginal / variance, index=tickers, name="Contribución al riesgo")


# ---------------------------------------------------------- risk-adjusted
def sharpe_ratio(returns: pd.Series, rf: float = 0.0) -> float | None:
    """Annualized Sharpe ratio (QuantStats; ``rf`` yearly)."""
    if not _enough(returns, 3) or returns.std() == 0:
        return None
    return _number(qs.stats.sharpe(returns, rf=rf, periods=TRADING_DAYS))


def sortino_ratio(returns: pd.Series, rf: float = 0.0) -> float | None:
    """Annualized Sortino ratio (QuantStats; downside deviation, target 0)."""
    if not _enough(returns, 3) or not (returns < 0).any():
        return None
    return _number(qs.stats.sortino(returns, rf=rf, periods=TRADING_DAYS))


def calmar_ratio(values: pd.Series) -> float | None:
    growth = cagr(values)
    worst = drawdown_stats(values)["max"]
    if growth is None or not worst:
        return None
    return float(growth / abs(worst))


def rolling_sharpe(returns: pd.Series, window: int = ROLLING_SHARPE_WINDOW, rf: float = 0.0) -> pd.Series:
    excess = returns - rf / TRADING_DAYS
    ratio = excess.rolling(window).mean() / excess.rolling(window).std(ddof=1) * TRADING_DAYS**0.5
    return ratio.replace([math.inf, -math.inf], math.nan).dropna()


def negative_streak(series: pd.Series) -> int:
    """Number of most recent consecutive observations below zero."""
    streak = 0
    for value in reversed(series.dropna().to_list()):
        if value >= 0:
            break
        streak += 1
    return streak


# -------------------------------------------------------- vs. benchmark
def benchmark_metrics(returns: pd.Series, benchmark: pd.Series, rf: float = 0.0) -> dict:
    """Beta, Jensen's alpha (annual), tracking error, information ratio, captures."""
    pair = pd.concat([returns.rename("p"), benchmark.rename("b")], axis=1, sort=True).dropna()
    keys = ("beta", "alpha", "tracking_error", "information_ratio", "upside_capture", "downside_capture")
    if len(pair) < 3:
        return dict.fromkeys(keys)
    p, b = pair["p"], pair["b"]
    variance = b.var(ddof=1)
    beta = float(p.cov(b) / variance) if variance > 0 else None
    daily_rf = rf / TRADING_DAYS
    alpha = float(((p.mean() - daily_rf) - beta * (b.mean() - daily_rf)) * TRADING_DAYS) if beta is not None else None
    active = p - b
    tracking_error = float(active.std(ddof=1) * TRADING_DAYS**0.5)
    up, down = b > 0, b < 0
    return {
        "beta": beta,
        "alpha": alpha,
        "tracking_error": tracking_error,
        "information_ratio": float(active.mean() * TRADING_DAYS / tracking_error) if tracking_error > 0 else None,
        "upside_capture": float(p[up].mean() / b[up].mean()) if up.any() else None,
        "downside_capture": float(p[down].mean() / b[down].mean()) if down.any() else None,
    }


# ------------------------------------------------------------- structure
def herfindahl(weights: pd.Series) -> float | None:
    """Herfindahl-Hirschman index of the absolute weights (1/N .. 1)."""
    weights = weights.dropna().abs()
    total = weights.sum()
    if total <= 0:
        return None
    return float(((weights / total) ** 2).sum())


def effective_assets(weights: pd.Series) -> float | None:
    index = herfindahl(weights)
    return 1 / index if index else None


def rolling_average_correlation(asset_returns: pd.DataFrame, window: int = ROLLING_CORRELATION_WINDOW) -> pd.Series:
    """Mean pairwise correlation of the assets over a rolling window."""
    returns = asset_returns.dropna(axis="columns", how="all")
    columns = list(returns.columns)
    if len(columns) < 2 or len(returns) < window:
        return pd.Series(dtype=float)
    pairs = [
        returns[first].rolling(window).corr(returns[second])
        for position, first in enumerate(columns)
        for second in columns[position + 1 :]
    ]
    return pd.concat(pairs, axis=1).mean(axis=1).dropna().rename("Correlación promedio")


def tracking_metrics(
    values: pd.Series,
    holdings: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark: pd.Series,
    target: dict[str, float],
    *,
    rf: float = 0.0,
    turnover: float = 0.0,
    raw_prices: pd.DataFrame | None = None,
) -> dict:
    """Every dashboard metric (``scalars``) plus the series used in charts.

    Inputs are daily on business days; ``turnover`` is the cumulative turnover
    of the portfolio changes. ``raw_prices`` (not forward-filled) measures how
    many business days each held asset has gone without a new price. Scalars
    without enough history are ``None``.
    """
    returns = daily_returns(values)
    benchmark_returns = daily_returns(benchmark.reindex(values.index).ffill().dropna())
    asset_returns = prices.reindex(values.index).ffill().pct_change(fill_method=None).iloc[1:]
    weights = current_weights(holdings)
    drift = weight_drift(weights, target)
    drawdown = drawdown_stats(values)
    rolling_vol = {window: rolling_volatility(returns, window) for window in ROLLING_VOL_WINDOWS}
    sharpe_series = rolling_sharpe(returns, ROLLING_SHARPE_WINDOW, rf)
    held = [ticker for ticker in weights.index if ticker in asset_returns.columns]
    correlation = rolling_average_correlation(asset_returns[held])
    risk = risk_contributions(asset_returns, weights)
    last = lambda series: _number(series.iloc[-1]) if not series.empty else None  # noqa: E731
    stale = stale_days(raw_prices.reindex(columns=held), values.index) if raw_prices is not None and held else pd.Series(dtype=float)
    scalars = {
        "cumulative_return": cumulative_return(values),
        **{f"return_{key}": value for key, value in period_returns(values).items()},
        "cagr": cagr(values),
        "volatility": annualized_volatility(returns),
        "rolling_volatility_21": last(rolling_vol[21]),
        "rolling_volatility_63": last(rolling_vol[63]),
        "average_rolling_volatility_21": _number(rolling_vol[21].mean()) if not rolling_vol[21].empty else None,
        "average_rolling_volatility_63": _number(rolling_vol[63].mean()) if not rolling_vol[63].empty else None,
        "drawdown": drawdown["current"],
        "max_drawdown": drawdown["max"],
        "max_drawdown_days": drawdown["max_duration_days"],
        "drawdown_days": drawdown["current_duration_days"],
        "var_95": historical_var(returns),
        "cvar_95": historical_cvar(returns),
        "semideviation": semideviation(returns),
        "sharpe": sharpe_ratio(returns, rf),
        "sortino": sortino_ratio(returns, rf),
        "calmar": calmar_ratio(values),
        "rolling_sharpe_126": last(sharpe_series),
        "negative_sharpe_days": negative_streak(sharpe_series),
        **benchmark_metrics(returns, benchmark_returns, rf),
        "herfindahl": herfindahl(weights),
        "effective_assets": effective_assets(weights),
        "max_abs_drift": _number(drift.abs().max()) if not drift.empty else None,
        "average_correlation": last(correlation),
        "max_risk_share": _number(risk.max()) if not risk.empty else None,
        "max_stale_days": int(stale.max()) if not stale.empty else 0,
        "turnover": turnover,
        "observations": len(returns),
    }
    return {
        "scalars": {
            key: value if isinstance(value, int) or value is None else _number(value)
            for key, value in scalars.items()
        },
        "series": {
            "returns": returns,
            "benchmark_returns": benchmark_returns,
            "drawdown": drawdown_series(values),
            "rolling_volatility": rolling_vol,
            "rolling_sharpe": sharpe_series,
            "average_correlation": correlation,
        },
        "weights": weights,
        "drift": drift,
        "return_contributions": return_contributions(holdings),
        "risk_contributions": risk,
        "context": {
            "drift_ticker": str(drift.abs().idxmax()) if not drift.empty else None,
            "risk_ticker": str(risk.idxmax()) if not risk.empty else None,
            "stale_ticker": str(stale.idxmax()) if not stale.empty and stale.max() > 0 else None,
        },
    }


def stale_days(raw_prices: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.Series:
    """Business days of ``calendar`` after the last real price of each column."""
    days = {}
    for ticker in raw_prices.columns:
        last_valid = raw_prices[ticker].last_valid_index()
        days[ticker] = len(calendar) if last_valid is None else int((calendar > last_valid).sum())
    return pd.Series(days, dtype=int)
