"""Monitoring workflows: price/risk analysis and company fundamentals."""

from datetime import date
from math import isfinite

import numpy as np
import pandas as pd

from optimizacion_portafolios.analytics.statistics import (
    calculate_expected_returns,
    calculate_statistics,
)
from optimizacion_portafolios.analytics.volatility import (
    calculate_historical_volatility,
    calculate_monthly_volatility,
)
from optimizacion_portafolios.data.market_data import (
    download_prices,
    fetch_fundamental_information,
)
from optimizacion_portafolios.web.common.charts import (
    line_chart,
    period_volatility_histograms,
    volatility_histogram,
)
from optimizacion_portafolios.web.common.tables import (
    dataframe_html,
    format_decimal,
    format_percent,
)

_FUNDAMENTAL_METRICS = (
    ("Precio actual", "currentPrice", "currency"),
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


def build_fundamental_analysis(ticker: str) -> dict | None:
    """Return the company profile view model, or None if Yahoo sent nothing."""
    info = fetch_fundamental_information(ticker)
    if not info:
        return None
    currency = str(info.get("currency") or "USD")
    indicators = []
    for label, key, kind in _FUNDAMENTAL_METRICS:
        value = info.get(key)
        if key == "currentPrice" and value is None:
            value = info.get("regularMarketPrice")
        indicators.append(
            {"label": label, "value": _format_fundamental_value(value, kind, currency)}
        )
    fundamentals = pd.DataFrame(
        [
            {
                "Indicador": label,
                "Valor": _format_fundamental_value(info.get(key), kind, currency),
            }
            for label, key, kind in _FUNDAMENTAL_ROWS
        ]
    )
    low, high = info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "Sector no disponible",
        "industry": info.get("industry") or "Industria no disponible",
        "country": info.get("country") or "País no disponible",
        "summary": info.get("longBusinessSummary"),
        "indicators": indicators,
        "annual_range": (
            f"{_format_fundamental_value(low, 'currency', currency)} – "
            f"{_format_fundamental_value(high, 'currency', currency)}"
            if low is not None and high is not None
            else "N/D"
        ),
        "table": dataframe_html(fundamentals, index=False),
    }


def build_price_analysis(
    view: str,
    tickers: list[str],
    start_date: date,
    period: str,
    volatility_ticker: str,
) -> dict:
    """Return the price/risk view model for the monitoring page.

    ``view`` is ``"options"`` (period-volatility histograms for every asset) or
    ``"portfolio"`` (monthly-volatility histogram for ``volatility_ticker``,
    falling back to the first asset when it has no data).
    ``missing`` lists requested tickers Yahoo Finance did not return.
    """
    prices = download_prices(tuple(tickers), start_date, date.today())
    prices = prices.dropna(axis="columns", how="all").ffill().dropna()
    if prices.empty or prices.shape[1] == 0:
        raise ValueError(
            "Yahoo Finance no devolvió precios para los activos y fechas seleccionados."
        )
    missing = sorted(set(tickers) - set(prices.columns))
    returns = prices.pct_change(fill_method=None).dropna(how="all")
    expected = calculate_expected_returns(prices)
    charts = {
        "prices": line_chart(prices, "Precios ajustados", "Precio de cierre"),
        "returns": line_chart(returns, "Retornos históricos", "Retorno diario"),
    }
    tables = {
        "prices": dataframe_html(
            prices.tail(60), {ticker: format_decimal for ticker in prices}
        ),
        "returns": dataframe_html(
            returns.tail(60), {ticker: format_percent for ticker in returns}
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
    volatility_tickers = []
    has_period_volatility = True
    if view == "options":
        period_vol, annualized_vol = calculate_historical_volatility(
            returns, period, prices.index.min(), prices.index.max()
        )
        has_period_volatility = not annualized_vol.empty
        if has_period_volatility:
            charts["volatility"] = period_volatility_histograms(
                annualized_vol, period_vol, period
            )
            latest = pd.DataFrame(
                {
                    f"VH {period.lower()}": period_vol.iloc[-1],
                    "VH anualizada": annualized_vol.iloc[-1],
                }
            )
            tables["volatility"] = dataframe_html(
                latest, {column: format_percent for column in latest.columns}
            )
    else:
        monthly_vol = calculate_monthly_volatility(returns)
        current_vol = returns.tail(21).std() * np.sqrt(21)
        if not monthly_vol.empty:
            volatility_tickers = list(monthly_vol.columns)
            if volatility_ticker not in monthly_vol.columns:
                volatility_ticker = monthly_vol.columns[0]
            charts["volatility"] = volatility_histogram(
                monthly_vol[volatility_ticker],
                volatility_ticker,
                current_vol.get(volatility_ticker, np.nan),
            )
        tables["volatility"] = dataframe_html(
            current_vol.rename("Volatilidad mensual actual").to_frame(),
            {"Volatilidad mensual actual": format_percent},
        )
    return {
        "charts": charts,
        "tables": tables,
        "tickers": list(prices.columns),
        "missing": missing,
        "start": prices.index.min().date().isoformat(),
        "end": prices.index.max().date().isoformat(),
        "observations": len(prices),
        "volatility_tickers": volatility_tickers,
        "selected_volatility_ticker": volatility_ticker,
        "has_period_volatility": has_period_volatility,
    }
