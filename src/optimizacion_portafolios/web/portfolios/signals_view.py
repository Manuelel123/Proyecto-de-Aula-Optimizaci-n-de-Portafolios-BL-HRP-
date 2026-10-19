"""Signals page of a tracked portfolio (suggestions; they never change it)."""

from __future__ import annotations

from datetime import date, timedelta
from math import isfinite

import pandas as pd
from flask import request

from optimizacion_portafolios.models.signals import SignalConfig, run_signals
from optimizacion_portafolios.storage.models import Portfolio
from optimizacion_portafolios.web.common.charts import strategy_drawdown_chart, strategy_equity_chart
from optimizacion_portafolios.web.common.tables import dataframe_html, format_percent
from optimizacion_portafolios.web.portfolios.services import DOWNLOAD_ERRORS, usd_prices

# Calendar days per trading day when downloading the warm-up history.
_CALENDAR_PER_TRADING_DAY = 1.5
DEFAULTS = {
    "fast_ma": "50",
    "slow_ma": "200",
    "use_trend": "1",
    "vol_window": "21",
    "vol_multiple": "2",
    "drawdown_exit": "20",
    "stop_loss": "10",
    "trailing_stop": "1",
}


def form_values() -> dict[str, str]:
    """Submitted settings (query string) or the defaults on the first visit."""
    if "submitted" not in request.args:
        return dict(DEFAULTS)
    values = {field: request.args.get(field, "").strip() for field in DEFAULTS}
    for flag in ("use_trend", "trailing_stop"):
        values[flag] = "1" if request.args.get(flag) else ""
    return values


def _number(values: dict, field: str, label: str, *, integer: bool = False, percent: bool = False):
    raw = values.get(field, "").replace(",", ".")
    if not raw:
        return None
    try:
        number = int(raw) if integer else float(raw)
    except ValueError as error:
        raise ValueError(f"{label} debe ser {'un entero' if integer else 'numérico'}.") from error
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{label} debe ser positivo.")
    return number / 100 if percent else number


def config_from(values: dict, commission_bps: float) -> SignalConfig:
    return SignalConfig(
        fast_ma=_number(values, "fast_ma", "La media rápida", integer=True) or 50,
        slow_ma=_number(values, "slow_ma", "La media lenta", integer=True) or 200,
        use_trend=bool(values.get("use_trend")),
        vol_window=_number(values, "vol_window", "La ventana de volatilidad", integer=True) or 21,
        vol_multiple=_number(values, "vol_multiple", "El multiplicador de volatilidad"),
        drawdown_exit=_number(values, "drawdown_exit", "El drawdown de salida", percent=True),
        stop_loss=_number(values, "stop_loss", "El stop-loss", percent=True),
        trailing_stop=bool(values.get("trailing_stop")),
        fees=commission_bps / 10_000,
    )


def signals_view(portfolio: Portfolio, config: SignalConfig, today: date | None = None) -> dict:
    """Simulation of the current version's assets since the fixing date."""
    today = today or date.today()
    version = portfolio.current_version
    start = portfolio.tracking_start
    history_start = start - timedelta(days=int(config.warmup_days * _CALENDAR_PER_TRADING_DAY) + 14)
    tickers = tuple(version.target_weights)
    try:
        prices = usd_prices(tickers, history_start, today)
    except DOWNLOAD_ERRORS as error:
        raise ValueError(f"No se pudieron descargar los precios: {error}") from error
    prices = prices.loc[prices.index.dayofweek < 5]
    result = run_signals(prices, version.target_weights, portfolio.initial_capital, pd.Timestamp(start), config)
    reference = "Comprar y mantener"
    metrics = result.metrics
    table = dataframe_html(
        metrics,
        {
            "Retorno total": format_percent,
            "Volatilidad anualizada": format_percent,
            "Máxima caída": format_percent,
            "Ratio de Sharpe": lambda value: "N/D" if pd.isna(value) else f"{value:.2f}",
            "Operaciones": lambda value: "—" if pd.isna(value) else f"{int(value)}",
        },
    )
    signals_return = float(metrics.loc["Señales", "Retorno total"])
    hold_return = float(metrics.loc[reference, "Retorno total"])
    return {
        "start": start.isoformat(),
        "end": result.values.index[-1].date().isoformat(),
        "signals_return": signals_return,
        "hold_return": hold_return,
        "difference": signals_return - hold_return,
        "metrics_table": table,
        "charts": {
            key: chart
            for key, chart in {
                "equity": strategy_equity_chart(result.values, reference),
                "drawdown": strategy_drawdown_chart(result.values, reference),
            }.items()
            if chart is not None
        },
        "trades": result.trades,
        "suggestions": result.suggestions,
    }
