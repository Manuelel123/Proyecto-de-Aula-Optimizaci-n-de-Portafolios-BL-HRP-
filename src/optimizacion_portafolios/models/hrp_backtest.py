"""Walk-forward backtest of the HRP portfolio with rebalancing triggers (vectorbt).

Timing convention (no look-ahead): an order executed at the close of day ``t``
only uses information available up to the close of ``t - 1``:

* the HRP weights are estimated with the ``lookback`` daily returns that end
  on ``t - 1`` (``returns.iloc[t - lookback:t]``);
* triggers are evaluated on the close of ``t - 1`` (weights after the price
  move of that day, rolling volatility through that day) and the rebalance is
  executed on the next close; calendar triggers fire on the first trading day
  of each new period.

The rebalance schedule of the path-dependent triggers (drift and volatility)
is decided with a frictionless simulation of the drifting weights; vectorbt
then executes every strategy with fees, slippage and cash in one call
(``Portfolio.from_orders`` with ``size_type="targetpercent"``, one group per
strategy and ``cash_sharing=True``).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics.performance import calculate_strategy_metrics
from optimizacion_portafolios.analytics.statistics import TRADING_DAYS
from optimizacion_portafolios.models.hrp import WeightBounds, optimize_hrp

logger = logging.getLogger(__name__)

BUY_AND_HOLD = "buy_hold"
CALENDAR_STRATEGIES = {
    "monthly": "Mensual",
    "quarterly": "Trimestral",
    "semiannual": "Semestral",
    "annual": "Anual",
}
DRIFT = "drift"
VOLATILITY = "volatility"
STRATEGY_KEYS = (BUY_AND_HOLD, *CALENDAR_STRATEGIES, DRIFT, VOLATILITY)
MIN_BACKTEST_DAYS = 20
VOL_HYSTERESIS = 0.10  # ±10 % band around the volatility threshold
MIN_LOOKBACK = 20
_CACHE_MAX_SIZE = 16


@dataclass(frozen=True)
class BacktestConfig:
    """Backtest parameters. Fractions: 0.05 = 5 % (or 5 percentage points).

    ``vol_threshold=None`` detects volatility regimes against the expanding
    median of the rolling volatility observed so far ("automático"; it waits
    ``vol_window`` observations before the first signal). Regime changes use a
    ±``VOL_HYSTERESIS`` band around the threshold to avoid whipsaw.
    """

    lookback: int = TRADING_DAYS
    drift_threshold: float = 0.05
    vol_window: int = 63
    vol_threshold: float | None = None
    fees: float = 0.001
    slippage: float = 0.0005
    init_cash: float = 10_000.0

    def __post_init__(self) -> None:
        if self.lookback < MIN_LOOKBACK:
            raise ValueError(
                f"La ventana de estimación debe tener al menos {MIN_LOOKBACK} días."
            )
        if self.vol_window < 5:
            raise ValueError("La ventana de volatilidad debe tener al menos 5 días.")
        if not 0 < self.drift_threshold < 1:
            raise ValueError("El umbral de desviación debe estar entre 0 % y 100 %.")
        if self.vol_threshold is not None and self.vol_threshold <= 0:
            raise ValueError("El umbral de volatilidad debe ser positivo.")
        if not (0 <= self.fees < 1 and 0 <= self.slippage < 1):
            raise ValueError("Las comisiones y el slippage deben estar entre 0 % y 100 %.")
        if not self.init_cash > 0:
            raise ValueError("El capital inicial debe ser positivo.")


def strategy_labels(config: BacktestConfig) -> dict[str, str]:
    """Display name of every strategy key, in presentation order."""
    vol = (
        f"{config.vol_threshold:.0%}".replace("%", " %")
        if config.vol_threshold is not None
        else "régimen"
    )
    return {
        BUY_AND_HOLD: "Buy & hold",
        **CALENDAR_STRATEGIES,
        DRIFT: f"Desviación ±{config.drift_threshold * 100:g} pp",
        VOLATILITY: f"Volatilidad ({vol})",
    }


@dataclass(frozen=True)
class BacktestResult:
    """Outcome of :func:`run_backtest`; frames are indexed by strategy label."""

    values: pd.DataFrame  # portfolio value per day (columns = strategy labels)
    metrics: pd.DataFrame  # one row per strategy
    rebalance_dates: dict[str, list[pd.Timestamp]]  # excludes the initial buy
    target_weights: dict[str, pd.DataFrame]  # HRP targets per execution date
    labels: dict[str, str]  # strategy key -> label
    config: BacktestConfig
    start: pd.Timestamp
    end: pd.Timestamp
    assets: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- schedule


def _period_key(strategy: str) -> Callable[[pd.Timestamp], tuple]:
    if strategy == "monthly":
        return lambda day: (day.year, day.month)
    if strategy == "quarterly":
        return lambda day: (day.year, (day.month - 1) // 3)
    if strategy == "semiannual":
        return lambda day: (day.year, (day.month - 1) // 6)
    if strategy == "annual":
        return lambda day: (day.year,)
    raise ValueError(f"Estrategia de calendario desconocida: {strategy}")


class WalkForwardWeights:
    """HRP weights estimated only with returns strictly before a position.

    ``weights(position)`` fits HRP on ``returns.iloc[position - lookback:position]``
    (the window ends on the previous day) and memoizes the result, so strategies
    sharing an execution date share the fit.
    """

    def __init__(
        self, returns: pd.DataFrame, lookback: int, bounds: WeightBounds | None
    ) -> None:
        self.returns = returns
        self.lookback = lookback
        self.bounds = (bounds or WeightBounds()).restricted_to(list(returns.columns))
        self._memo: dict[int, np.ndarray] = {}

    def window(self, position: int) -> pd.DataFrame:
        if position - self.lookback < 1:  # row 0 of pct_change is NaN
            raise ValueError("No hay suficientes datos previos para estimar HRP.")
        return self.returns.iloc[position - self.lookback : position]

    def weights(self, position: int) -> np.ndarray:
        if position not in self._memo:
            fitted = optimize_hrp(self.window(position), self.bounds).weights
            self._memo[position] = fitted.reindex(self.returns.columns).to_numpy(float)
        return self._memo[position]


def _rebalance_schedule(
    strategy: str,
    prices: pd.DataFrame,
    start: int,
    estimator: WalkForwardWeights,
    config: BacktestConfig,
) -> list[tuple[int, np.ndarray]]:
    """Execution positions and HRP targets: [(start, w0), (i1, w1), ...].

    Frictionless simulation of the holdings: ``pending`` is decided on the close
    of day ``i`` with information up to ``i`` and executed on ``i + 1``.
    """
    index = prices.index
    price_values = prices.to_numpy(float)
    returns_values = estimator.returns.to_numpy(float)
    target = estimator.weights(start)
    events = [(start, target)]
    holdings = target.copy()
    period_key = _period_key(strategy) if strategy in CALENDAR_STRATEGIES else None
    vol_history: list[float] = []
    regime: bool | None = None

    def decide(i: int, weights: np.ndarray) -> bool:
        nonlocal regime
        if strategy == BUY_AND_HOLD:
            return False
        if period_key is not None:
            return i + 1 < len(index) and period_key(index[i + 1]) != period_key(index[i])
        if strategy == DRIFT:
            return bool(np.max(np.abs(weights - target)) > config.drift_threshold + 1e-12)
        if strategy == VOLATILITY:
            window = returns_values[max(i - config.vol_window + 1, 1) : i + 1]
            if len(window) < config.vol_window:
                return False
            vol = float(np.std(window @ weights, ddof=1) * np.sqrt(TRADING_DAYS))
            vol_history.append(vol)
            if config.vol_threshold is not None:
                threshold = config.vol_threshold
            elif len(vol_history) >= config.vol_window:
                threshold = float(np.median(vol_history))
            else:
                return False  # automatic threshold still warming up
            if regime is None:
                regime = vol > threshold
                return False
            # Hysteresis band: enter the high regime above threshold·(1 + h),
            # leave it below threshold·(1 − h).
            if not regime and vol > threshold * (1 + VOL_HYSTERESIS):
                regime = True
                return True
            if regime and vol < threshold * (1 - VOL_HYSTERESIS):
                regime = False
                return True
            return False
        raise ValueError(f"Estrategia desconocida: {strategy}")

    pending = decide(start, holdings / holdings.sum())
    for i in range(start + 1, len(index)):
        holdings = holdings * (price_values[i] / price_values[i - 1])
        if pending:
            target = estimator.weights(i)
            holdings = target * holdings.sum()
            events.append((i, target))
        pending = decide(i, holdings / holdings.sum())
    return events


def backtest_start(index: pd.DatetimeIndex, lookback: int, start_date=None) -> int:
    """First position with a full estimation window (and on/after ``start_date``)."""
    position = lookback + 1
    if start_date is not None:
        position = max(position, int(index.searchsorted(pd.Timestamp(start_date))))
    if position >= len(index) - MIN_BACKTEST_DAYS:
        raise ValueError(
            "No hay datos suficientes para el backtest: se necesitan "
            f"{lookback} retornos para estimar HRP más al menos "
            f"{MIN_BACKTEST_DAYS} días de prueba."
        )
    return position


# --------------------------------------------------------------------------- vectorbt


def _vectorbt():
    import vectorbt as vbt  # heavy import (numba); loaded lazily

    return vbt


def _simulate(
    prices: pd.DataFrame,
    schedules: dict[str, list[tuple[int, np.ndarray]]],
    start: int,
    config: BacktestConfig,
):
    """Run every strategy in one vectorbt call; one column group per strategy."""
    vbt = _vectorbt()
    window = prices.iloc[start:]
    keys = list(schedules)
    columns = pd.MultiIndex.from_product(
        [keys, list(prices.columns)], names=["strategy", "asset"]
    )
    close = pd.DataFrame(
        np.tile(window.to_numpy(float), len(keys)), index=window.index, columns=columns
    )
    size = np.full(close.shape, np.nan)
    assets = prices.shape[1]
    for number, key in enumerate(keys):
        for position, weights in schedules[key]:
            size[position - start, number * assets : (number + 1) * assets] = weights
    portfolio = vbt.Portfolio.from_orders(
        close,
        pd.DataFrame(size, index=close.index, columns=columns),
        size_type="targetpercent",
        group_by="strategy",
        cash_sharing=True,
        call_seq="auto",
        fees=float(config.fees),
        slippage=float(config.slippage),
        init_cash=float(config.init_cash),
        freq="1D",
    )
    return portfolio, close


def _order_costs(portfolio, close: pd.DataFrame) -> pd.DataFrame:
    """Fees and slippage cost (|fill − close| × size) per strategy key."""
    records = portfolio.orders.values
    strategies = close.columns.get_level_values("strategy")
    if len(records) == 0:
        return pd.DataFrame(
            0.0, index=pd.Index(pd.unique(strategies)), columns=["fees", "slippage", "orders"]
        )
    reference = close.to_numpy(float)[records["idx"], records["col"]]
    frame = pd.DataFrame(
        {
            "strategy": strategies[records["col"]],
            "fees": records["fees"],
            "slippage": np.abs(records["price"] - reference) * records["size"],
            "orders": 1,
        }
    )
    return frame.groupby("strategy", sort=False)[["fees", "slippage", "orders"]].sum()


def run_backtest(
    prices: pd.DataFrame,
    config: BacktestConfig | None = None,
    bounds: WeightBounds | None = None,
    start_date=None,
    strategies: tuple[str, ...] = STRATEGY_KEYS,
) -> BacktestResult:
    """Walk-forward HRP backtest of ``strategies`` on aligned close ``prices``.

    ``prices`` must be gap-free (ffilled and without NaN). The test period starts
    on the first day on/after ``start_date`` that has ``lookback`` returns before
    it, so all strategies share the same start, capital and initial HRP weights.
    """
    config = config or BacktestConfig()
    prices = prices.astype(float)
    if prices.shape[1] < 2:
        raise ValueError("El backtest necesita al menos dos activos.")
    if prices.isna().to_numpy().any():
        raise ValueError("Los precios del backtest no pueden tener datos faltantes.")
    start = backtest_start(prices.index, config.lookback, start_date)
    estimator = WalkForwardWeights(
        prices.pct_change(fill_method=None), config.lookback, bounds
    )
    schedules = {
        key: _rebalance_schedule(key, prices, start, estimator, config)
        for key in strategies
    }
    portfolio, close = _simulate(prices, schedules, start, config)
    _engine_ready.set()
    labels = {key: label for key, label in strategy_labels(config).items() if key in schedules}

    values = portfolio.value()
    if isinstance(values, pd.Series):  # a single group comes back as a Series
        values = values.to_frame(next(iter(schedules)))
    values = values.loc[:, list(schedules)].rename(columns=labels)
    costs = _order_costs(portfolio, close)
    index = prices.index
    rebalance_dates = {
        labels[key]: [index[position] for position, _ in events[1:]]
        for key, events in schedules.items()
    }
    target_weights = {
        labels[key]: pd.DataFrame(
            [weights for _, weights in events],
            index=pd.DatetimeIndex([index[position] for position, _ in events], name="Fecha"),
            columns=prices.columns,
        )
        for key, events in schedules.items()
    }
    rows = {}
    for key, label in labels.items():
        metrics = calculate_strategy_metrics(values[label], config.init_cash)
        fees = float(costs["fees"].get(key, 0.0))
        slippage = float(costs["slippage"].get(key, 0.0))
        metrics.update(
            {
                "Rebalanceos": len(rebalance_dates[label]),
                "Órdenes": int(costs["orders"].get(key, 0)),
                "Comisiones": fees,
                "Costo de slippage": slippage,
                "Costos totales": fees + slippage,
                "Costos / capital inicial": (fees + slippage) / config.init_cash,
            }
        )
        rows[label] = metrics
    metrics_frame = pd.DataFrame.from_dict(rows, orient="index")
    metrics_frame.index.name = "Estrategia"
    return BacktestResult(
        values=values,
        metrics=metrics_frame,
        rebalance_dates=rebalance_dates,
        target_weights=target_weights,
        labels=labels,
        config=config,
        start=index[start],
        end=index[-1],
        assets=list(prices.columns),
    )


# --------------------------------------------------------------------------- cache & warm-up

_cache: "OrderedDict[tuple, BacktestResult]" = OrderedDict()
_cache_lock = threading.Lock()


def _bounds_key(bounds: WeightBounds | None) -> tuple:
    bounds = bounds or WeightBounds()
    return (
        bounds.min_weight,
        bounds.max_weight,
        tuple(sorted(bounds.asset_min_weights.items())),
        tuple(sorted(bounds.asset_max_weights.items())),
    )


def cache_key(prices: pd.DataFrame, config, bounds, start_date, strategies) -> tuple:
    """Identity of a backtest: data fingerprint plus every parameter."""
    fingerprint = int(pd.util.hash_pandas_object(prices, index=True).sum())
    return (
        tuple(map(str, prices.columns)),
        fingerprint,
        config,
        _bounds_key(bounds),
        str(start_date),
        tuple(strategies),
    )


def cached_backtest(
    prices: pd.DataFrame,
    config: BacktestConfig | None = None,
    bounds: WeightBounds | None = None,
    start_date=None,
    strategies: tuple[str, ...] = STRATEGY_KEYS,
) -> tuple[BacktestResult, bool]:
    """``run_backtest`` memoized in a small LRU; returns ``(result, from_cache)``."""
    config = config or BacktestConfig()
    key = cache_key(prices, config, bounds, start_date, strategies)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key], True
    result = run_backtest(prices, config, bounds, start_date, strategies)
    with _cache_lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_SIZE:
            _cache.popitem(last=False)
    return result, False


def clear_backtest_cache() -> None:
    with _cache_lock:
        _cache.clear()


_engine_ready = threading.Event()
_warmup_started = threading.Lock()


def engine_ready() -> bool:
    """True once vectorbt is imported and its numba kernels are compiled."""
    return _engine_ready.is_set()


def warm_up() -> None:
    """Import vectorbt and compile the kernels used by ``_simulate`` (tiny run)."""
    index = pd.bdate_range("2020-01-01", periods=8)
    prices = pd.DataFrame(
        {"A": np.linspace(100, 107, 8), "B": np.linspace(50, 46.5, 8)}, index=index
    )
    schedules = {"warmup": [(0, np.array([0.5, 0.5])), (4, np.array([0.6, 0.4]))]}
    portfolio, close = _simulate(prices, schedules, 0, BacktestConfig())
    portfolio.value()
    _order_costs(portfolio, close)
    _engine_ready.set()


def warm_up_in_background() -> threading.Thread | None:
    """Start :func:`warm_up` once in a daemon thread (idempotent)."""
    if engine_ready() or not _warmup_started.acquire(blocking=False):
        return None

    def target() -> None:
        try:
            warm_up()
        except Exception:  # pragma: no cover - warm-up is best effort
            logger.exception("vectorbt warm-up failed")

    thread = threading.Thread(target=target, name="vectorbt-warmup", daemon=True)
    thread.start()
    return thread
