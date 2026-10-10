"""Monitoring workflows: price/risk analysis and company fundamentals."""

import logging
from datetime import date
from math import isfinite

import numpy as np
import pandas as pd
import requests
from yfinance.exceptions import YFException

from optimizacion_portafolios.analytics.statistics import (
    calculate_expected_returns,
    calculate_statistics,
    date_years_ago,
)
from optimizacion_portafolios.analytics.volatility import (
    calculate_historical_volatility,
    calculate_monthly_volatility,
)
from optimizacion_portafolios.data.catalogs import GRUPOS_ACTIVOS
from optimizacion_portafolios.data.market_data import (
    download_prices,
    fetch_fundamental_information,
)
from optimizacion_portafolios.web.common.charts import (
    period_volatility_chart,
    returns_box_chart,
    volatility_histogram_chart,
)
from optimizacion_portafolios.web.common.price_series import price_series_payload
from optimizacion_portafolios.web.common.tables import (
    dataframe_html,
    format_decimal,
    format_percent,
)

logger = logging.getLogger(__name__)
_RECENT_ROWS = 60
# Realized monthly volatility uses the last 21 sessions.
_MONTH_SESSIONS = 21

_FUNDAMENTAL_METRICS = (
    ("Capitalización bursátil", "marketCap", "billions"),
    ("P/E (últimos 12 meses)", "trailingPE", "multiple"),
    ("P/E proyectado", "forwardPE", "multiple"),
    ("Rendimiento por dividendo", "dividendYield", "percentage"),
)
_FUNDAMENTAL_ROWS = (
    ("Crecimiento de ingresos", "revenueGrowth", "percentage"),
    ("Margen bruto", "grossMargins", "percentage"),
    ("Margen operativo", "operatingMargins", "percentage"),
    ("Margen neto", "profitMargins", "percentage"),
    ("Retorno sobre patrimonio (ROE)", "returnOnEquity", "percentage"),
    ("Deuda / capital", "debtToEquity", "percentage"),
    ("Razón corriente", "currentRatio", "multiple"),
    ("Ingresos", "totalRevenue", "billions"),
    ("Utilidad neta", "netIncomeToCommon", "billions"),
    ("Flujo de caja libre", "freeCashflow", "billions"),
)


def asset_names() -> dict[str, str]:
    """Display label per ticker from the catalogs (first group wins)."""
    names: dict[str, str] = {}
    for assets in GRUPOS_ACTIVOS.values():
        for label, ticker in assets.items():
            names.setdefault(ticker, label)
    return names


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _format_fundamental_value(value, kind: str, currency: str) -> str:
    if value is None:
        return "N/D"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not isfinite(number):
        return "N/D"
    if kind == "currency":
        return f"{currency} {number:,.2f}"
    if kind == "billions":
        return f"{currency} {number / 1_000_000_000:,.2f} mil millones"
    if kind == "percentage":
        return f"{number:.2%}"
    if kind == "multiple":
        return f"{number:.2f}x"
    return f"{number:,.2f}"


def _fundamental_prices(ticker: str, name: str):
    """One-year price explorer payload for the company; None if unavailable."""
    today = date.today()
    try:
        prices = download_prices((ticker,), date_years_ago(today), today)
    except (requests.RequestException, TimeoutError, YFException, ValueError):
        logger.warning(
            "Could not download prices for %s fundamentals", ticker, exc_info=True
        )
        return None
    if prices.empty or ticker not in prices:
        return None
    return price_series_payload(prices[[ticker]], {ticker: name})


def build_fundamental_analysis(ticker: str) -> dict | None:
    """Return the company profile view model, or None if Yahoo sent nothing.

    Keys: ``ticker``, ``name``, ``currency``, ``sector``, ``industry``,
    ``country``, ``summary``, ``price`` (formatted), ``change`` (``None`` or
    ``{"value": "1.23%", "direction": "up"|"down"}`` against the previous
    close), ``indicators`` (KPI cards ``label``/``value``), ``annual_range``,
    ``table`` (HTML) and ``prices_payload`` (one-year price explorer JSON, or
    ``None`` when prices are unavailable).
    """
    info = fetch_fundamental_information(ticker)
    if not info:
        return None
    currency = str(info.get("currency") or "USD")
    indicators = [
        {
            "label": label,
            "value": _format_fundamental_value(info.get(key), kind, currency),
        }
        for label, key, kind in _FUNDAMENTAL_METRICS
    ]
    fundamentals = pd.DataFrame(
        [
            {
                "Indicador": label,
                "Valor": _format_fundamental_value(info.get(key), kind, currency),
            }
            for label, key, kind in _FUNDAMENTAL_ROWS
        ]
    )
    price = _number(info.get("currentPrice"))
    if price is None:
        price = _number(info.get("regularMarketPrice"))
    previous = _number(
        info.get("regularMarketPreviousClose") or info.get("previousClose")
    )
    change = None
    if price is not None and previous:
        variation = price / previous - 1
        change = {
            "value": f"{abs(variation):.2%}",
            "direction": "up" if variation >= 0 else "down",
        }
    low, high = info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
    name = info.get("longName") or info.get("shortName") or ticker
    return {
        "ticker": ticker,
        "name": name,
        "currency": currency,
        "sector": info.get("sector") or "Sector no disponible",
        "industry": info.get("industry") or "Industria no disponible",
        "country": info.get("country") or "País no disponible",
        "summary": info.get("longBusinessSummary"),
        "price": _format_fundamental_value(price, "currency", currency),
        "change": change,
        "indicators": indicators,
        "annual_range": (
            f"{_format_fundamental_value(low, 'currency', currency)} – "
            f"{_format_fundamental_value(high, 'currency', currency)}"
            if low is not None and high is not None
            else "N/D"
        ),
        "table": dataframe_html(fundamentals, index=False),
        "prices_payload": _fundamental_prices(ticker, name),
    }


def build_price_analysis(
    view: str,
    tickers: list[str],
    start_date: date,
    period: str,
) -> dict:
    """Return the price/risk view model for the monitoring page.

    ``view`` is ``"options"`` (period volatility for every asset) or
    ``"portfolio"`` (monthly-volatility distribution per asset).

    Keys: ``view``, ``prices_payload`` (price explorer JSON built from the raw,
    non forward-filled prices), ``charts`` (``returns_box``; plus
    ``monthly_volatility`` in the portfolio view or ``period_volatility`` and
    ``period_volatility_distribution`` in the options view), ``tables``
    (``prices``, ``returns``, ``expected``, ``statistics``, ``volatility``),
    ``tickers``, ``missing`` (requested tickers without data), ``start``,
    ``end``, ``observations``, ``period``, ``volatility_label`` and
    ``has_period_volatility``.
    """
    raw_prices = download_prices(tuple(tickers), start_date, date.today())
    raw_prices = raw_prices.dropna(axis="columns", how="all")
    prices = raw_prices.ffill().dropna()
    if prices.empty or prices.shape[1] == 0:
        raise ValueError(
            "Yahoo Finance no devolvió precios para los activos y fechas seleccionados."
        )
    missing = sorted(set(tickers) - set(prices.columns))
    returns = prices.pct_change(fill_method=None).dropna(how="all")
    expected = calculate_expected_returns(prices)
    charts = {"returns_box": returns_box_chart(returns)}
    tables = {
        "prices": dataframe_html(
            prices.tail(_RECENT_ROWS).iloc[::-1],
            {ticker: format_decimal for ticker in prices},
        ),
        "returns": dataframe_html(
            returns.tail(_RECENT_ROWS).iloc[::-1],
            {ticker: format_percent for ticker in returns},
        ),
        "expected": dataframe_html(
            expected, {column: format_percent for column in expected.columns}
        ),
        "statistics": dataframe_html(
            calculate_statistics(prices),
            {
                "Último precio": format_decimal,
                "Retorno total": format_percent,
                "Retorno anualizado": format_percent,
                "Volatilidad anualizada": format_percent,
                "Máxima caída": format_percent,
            },
        ),
    }
    has_period_volatility = True
    volatility_label = f"VH {period.lower()}"
    if view == "options":
        period_vol, annualized_vol = calculate_historical_volatility(
            returns, period, prices.index.min(), prices.index.max()
        )
        has_period_volatility = not annualized_vol.empty
        if has_period_volatility:
            charts["period_volatility"] = period_volatility_chart(
                annualized_vol, period_vol, period
            )
            charts["period_volatility_distribution"] = volatility_histogram_chart(
                annualized_vol, annualized_vol.iloc[-1]
            )
            latest = pd.DataFrame(
                {
                    volatility_label: period_vol.iloc[-1],
                    "VH anualizada": annualized_vol.iloc[-1],
                }
            ).sort_values("VH anualizada", ascending=False)
            tables["volatility"] = dataframe_html(
                latest, {column: format_percent for column in latest.columns}
            )
    else:
        monthly_vol = calculate_monthly_volatility(returns)
        current_vol = returns.tail(_MONTH_SESSIONS).std() * np.sqrt(_MONTH_SESSIONS)
        if not monthly_vol.empty:
            charts["monthly_volatility"] = volatility_histogram_chart(
                monthly_vol, current_vol
            )
        tables["volatility"] = dataframe_html(
            current_vol.rename("Volatilidad mensual actual").to_frame(),
            {"Volatilidad mensual actual": format_percent},
        )
    return {
        "view": view,
        "prices_payload": price_series_payload(raw_prices, asset_names()),
        "charts": {key: value for key, value in charts.items() if value is not None},
        "tables": tables,
        "tickers": list(prices.columns),
        "missing": missing,
        "start": prices.index.min().date().isoformat(),
        "end": prices.index.max().date().isoformat(),
        "observations": len(prices),
        "period": period,
        "volatility_label": volatility_label,
        "has_period_volatility": has_period_volatility,
    }
