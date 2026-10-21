"""Entry/exit signals per asset of a tracked portfolio, simulated with vectorbt.

Suggestions only: nothing here changes the portfolio. Each asset is "in" while
every enabled condition holds and "out" otherwise:

* trend: the fast moving average is above the slow one;
* volatility: its rolling volatility is at most ``vol_multiple`` times the
  median observed so far;
* drawdown: its fall from the running peak is smaller than ``drawdown_exit``.

Entries and exits are the switches of that state; a stop-loss (optionally
trailing) also closes a position. The signals portfolio is one vectorbt
``from_signals`` simulation of all assets together (``group_by`` and
``cash_sharing``): every entry buys ``target weight × capital`` of the asset
(vectorbt's signal simulator does not support target-percent sizes). Buy and
hold is the same capital bought at the target weights on the first day.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics.statistics import TRADING_DAYS

IN_POSITION, OUT_OF_POSITION = "Mantener", "Fuera"


@dataclass(frozen=True)
class SignalConfig:
    fast_ma: int = 50
    slow_ma: int = 200
    use_trend: bool = True
    vol_window: int = 21
    vol_multiple: float | None = 2.0
    drawdown_exit: float | None = 0.20
    stop_loss: float | None = 0.10
    trailing_stop: bool = True
    fees: float = 0.001

    def __post_init__(self) -> None:
        if self.use_trend and not 2 <= self.fast_ma < self.slow_ma:
            raise ValueError("La media rápida debe ser menor que la lenta (y de al menos 2 días).")
        if self.vol_window < 5:
            raise ValueError("La ventana de volatilidad debe tener al menos 5 días.")
        for name, value in (("multiplicador de volatilidad", self.vol_multiple), ("drawdown", self.drawdown_exit), ("stop-loss", self.stop_loss)):
            if value is not None and value <= 0:
                raise ValueError(f"El {name} debe ser positivo.")

    @property
    def warmup_days(self) -> int:
        """Trading days of history needed before the first signal."""
        return max(self.slow_ma if self.use_trend else 0, self.vol_window * 3)


@dataclass(frozen=True)
class SignalResult:
    values: pd.DataFrame  # "Señales" and "Comprar y mantener" portfolio values
    metrics: pd.DataFrame  # one row per strategy
    trades: list[dict]
    suggestions: list[dict]
    state: pd.DataFrame  # True while each asset is "in"


def asset_state(prices: pd.DataFrame, config: SignalConfig) -> pd.DataFrame:
    """Boolean frame: True while every enabled condition holds for the asset."""
    state = pd.DataFrame(True, index=prices.index, columns=prices.columns)
    if config.use_trend:
        fast = prices.rolling(config.fast_ma).mean()
        slow = prices.rolling(config.slow_ma).mean()
        state &= (fast > slow).where(slow.notna(), True)
    if config.vol_multiple is not None:
        volatility = prices.pct_change(fill_method=None).rolling(config.vol_window).std() * TRADING_DAYS**0.5
        median = volatility.expanding().median()
        state &= (volatility <= config.vol_multiple * median).where(volatility.notna(), True)
    if config.drawdown_exit is not None:
        drawdown = prices / prices.cummax() - 1
        state &= drawdown > -config.drawdown_exit
    return state.astype(bool)


def entry_exit(state: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Switches of ``state``; an asset already "in" on the first day enters then."""
    previous = state.shift(1, fill_value=False)
    entries = state & ~previous
    exits = ~state & previous
    return entries, exits


def _last_switch(state: pd.Series) -> pd.Timestamp:
    """Day the asset's state last changed (the first day when it never did)."""
    changed = state.ne(state.shift(1)).iloc[1:]
    return changed[changed].index[-1] if changed.any() else state.index[0]


def _metrics(values: pd.Series, trades: int | None) -> dict:
    returns = values.pct_change(fill_method=None).dropna()
    std = returns.std(ddof=1)
    return {
        "Retorno total": float(values.iloc[-1] / values.iloc[0] - 1),
        "Volatilidad anualizada": float(std * TRADING_DAYS**0.5) if len(returns) > 1 else np.nan,
        "Ratio de Sharpe": float(returns.mean() / std * TRADING_DAYS**0.5) if len(returns) > 2 and std > 0 else np.nan,
        "Máxima caída": float((values / values.cummax() - 1).min()),
        "Operaciones": trades,
    }


def run_signals(
    prices: pd.DataFrame,
    weights: dict[str, float],
    capital: float,
    start: pd.Timestamp,
    config: SignalConfig | None = None,
) -> SignalResult:
    """Simulate the signals from ``start`` (``prices`` include the warm-up)."""
    import vectorbt as vbt  # heavy import (numba); loaded lazily

    config = config or SignalConfig()
    tickers = [ticker for ticker in weights if ticker in prices.columns]
    if not tickers:
        raise ValueError("No hay precios de los activos del portafolio.")
    prices = prices.loc[:, tickers].sort_index().ffill().dropna(how="all")
    state = asset_state(prices, config).loc[prices.index >= start]
    close = prices.loc[prices.index >= start]
    if len(close) < 2:
        raise ValueError("Se necesitan al menos dos días desde la fecha de fijación para simular señales.")
    entries, exits = entry_exit(state)
    total = sum(weights[t] for t in tickers)
    target = np.array([weights[t] / total for t in tickers])
    signals = vbt.Portfolio.from_signals(
        close,
        entries,
        exits,
        size=target[None, :] * capital,
        size_type="value",
        init_cash=capital,
        fees=config.fees,
        sl_stop=config.stop_loss if config.stop_loss is not None else np.nan,
        sl_trail=config.trailing_stop,
        group_by=True,
        cash_sharing=True,
        call_seq="auto",
        freq="1D",
    )
    first_day = pd.DataFrame(np.nan, index=close.index, columns=tickers)
    first_day.iloc[0] = target
    hold = vbt.Portfolio.from_orders(
        close, size=first_day, size_type="targetpercent", init_cash=capital, fees=config.fees,
        group_by=True, cash_sharing=True, call_seq="auto", freq="1D",
    )
    values = pd.DataFrame({"Señales": signals.value(), "Comprar y mantener": hold.value()})
    trades = signals.trades.records_readable
    trade_rows = [
        {
            "ticker": str(row["Column"]),
            "entry": pd.Timestamp(row["Entry Timestamp"]).date().isoformat(),
            "exit": pd.Timestamp(row["Exit Timestamp"]).date().isoformat() if row["Status"] == "Closed" else None,
            "entry_price": float(row["Avg Entry Price"]),
            "exit_price": float(row["Avg Exit Price"]) if row["Status"] == "Closed" else None,
            "return": float(row["Return"]),
            "status": "Cerrada" if row["Status"] == "Closed" else "Abierta",
        }
        for _, row in trades.iterrows()
    ]
    metrics = pd.DataFrame(
        {
            "Señales": _metrics(values["Señales"], len(trade_rows)),
            "Comprar y mantener": _metrics(values["Comprar y mantener"], None),
        }
    ).T
    open_columns = {row["ticker"] for row in trade_rows if row["status"] == "Abierta"}
    last = state.iloc[-1]
    suggestions = [
        {
            "ticker": ticker,
            "state": IN_POSITION if last[ticker] else OUT_OF_POSITION,
            "position": "Abierta" if ticker in open_columns else "Cerrada",
            "last_switch": _last_switch(state[ticker]).date().isoformat(),
        }
        for ticker in tickers
    ]
    return SignalResult(values=values, metrics=metrics, trades=trade_rows, suggestions=suggestions, state=state)
