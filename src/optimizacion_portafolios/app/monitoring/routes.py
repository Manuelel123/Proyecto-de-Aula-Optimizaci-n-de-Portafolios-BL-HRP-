"""Asset monitoring and company-fundamentals routes."""

import logging
from datetime import date
from math import isfinite

import matplotlib
import numpy as np
import pandas as pd
import requests
from flask import flash, render_template, request
from yfinance.exceptions import YFException

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from optimizacion_portafolios.analytics import (
    calculate_expected_returns,
    calculate_historical_volatility,
    calculate_monthly_volatility,
    calculate_statistics,
    date_one_year_ago,
)
from optimizacion_portafolios.catalogs import (
    ACTIVOS_OPCIONES,
    PERIODOS_VOLATILIDAD,
    PORTAFOLIOS_MONITOREO,
)
from optimizacion_portafolios.market_data import (
    download_prices,
    fetch_fundamental_information,
)
from optimizacion_portafolios.app.common.charts import figure_to_data_uri
from optimizacion_portafolios.app.common.forms import (
    parse_start_date,
    selected_option,
    selected_tickers,
)
from optimizacion_portafolios.app.monitoring import bp

logger = logging.getLogger(__name__)
_VIEWS = {"options", "portfolio", "fundamental"}
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


def _html_table(
    frame: pd.DataFrame,
    formatters: dict | None = None,
    index: bool = True,
) -> str:
    return frame.to_html(
        classes="data-table",
        border=0,
        escape=True,
        index=index,
        na_rep="N/D",
        formatters=formatters,
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


def _monitoring_chart(data: pd.DataFrame, title: str, ylabel: str) -> str:
    figure, axis = plt.subplots(figsize=(10, 3.8))
    data.plot(ax=axis, linewidth=1.25)
    axis.set_title(title, loc="left", fontweight="bold")
    axis.set_ylabel(ylabel)
    axis.set_xlabel("")
    axis.grid(axis="y", alpha=0.2)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, ncol=min(4, len(data.columns)))
    figure.tight_layout()
    return figure_to_data_uri(figure)


def _volatility_histograms(
    annualized_volatility: pd.DataFrame,
    period_volatility: pd.DataFrame,
    period_label: str,
    tickers: list[str],
) -> str:
    available = [
        ticker
        for ticker in tickers
        if ticker in annualized_volatility
        and annualized_volatility[ticker].notna().any()
    ]
    columns = 3
    rows = (len(available) + columns - 1) // columns
    figure, axes = plt.subplots(
        rows, columns, figsize=(15, 3.2 * rows), squeeze=False
    )
    for axis, ticker in zip(axes.flat, available):
        values = annualized_volatility[ticker].dropna() * 100
        latest_date = values.index[-1]
        latest_period = period_volatility.loc[latest_date, ticker]
        axis.hist(values, bins="auto", color="#287d6b", edgecolor="white")
        axis.axvline(values.iloc[-1], color="#d26045", linestyle="--", linewidth=1.5)
        axis.set_title(ticker, loc="left")
        axis.set_xlabel("Volatilidad anualizada (%)")
        axis.set_ylabel("Frecuencia")
        axis.text(
            0.97,
            0.95,
            f"Periodo: {latest_period:.2%}\nAnualizada: {values.iloc[-1] / 100:.2%}",
            transform=axis.transAxes,
            ha="right",
            va="top",
            fontsize=8,
            bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85},
        )
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", alpha=0.18)
    for axis in list(axes.flat)[len(available) :]:
        axis.set_visible(False)
    figure.suptitle(f"Volatilidad histórica · {period_label}", x=0.02, ha="left")
    figure.tight_layout()
    return figure_to_data_uri(figure)


def _single_histogram(volatility: pd.Series, ticker: str, current: float) -> str:
    figure, axis = plt.subplots(figsize=(9, 3.5))
    axis.hist(volatility.dropna() * 100, bins="auto", color="#287d6b", edgecolor="white")
    if pd.notna(current):
        axis.axvline(
            current * 100,
            color="#d26045",
            linestyle="--",
            label=f"Actual: {current:.2%}",
        )
        axis.legend(frameon=False)
    axis.set_title(f"Distribución de volatilidad mensual · {ticker}", loc="left")
    axis.set_xlabel("Volatilidad mensual (%)")
    axis.set_ylabel("Número de meses")
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", alpha=0.18)
    figure.tight_layout()
    return figure_to_data_uri(figure)


@bp.route("/monitoring", methods=["GET", "POST"])
def monitoring():
    view = request.values.get("view", "portfolio")
    if view not in _VIEWS:
        flash("La sección de monitoreo solicitada no existe.", "error")
        view = "portfolio"

    today = date.today()
    default_start = date_one_year_ago(today)
    portfolio_names = list(PORTAFOLIOS_MONITOREO)
    portfolio_name = request.values.get("portfolio", portfolio_names[0])
    if portfolio_name not in PORTAFOLIOS_MONITOREO:
        flash("Selecciona un portafolio válido.", "error")
        portfolio_name = portfolio_names[0]
    selected_portfolio = PORTAFOLIOS_MONITOREO[portfolio_name]
    portfolio_tickers = list(selected_portfolio.values())
    default_ticker = next(iter(selected_portfolio.values()))
    selected_ticker = request.values.get("ticker", default_ticker)
    selected_volatility_ticker = request.values.get(
        "volatility_ticker", portfolio_tickers[0]
    )
    if selected_volatility_ticker not in portfolio_tickers:
        selected_volatility_ticker = portfolio_tickers[0]
    period = request.values.get("period", "Mensual")
    if period not in PERIODOS_VOLATILIDAD:
        period = "Mensual"
    start_value = request.values.get("start_date", default_start.isoformat())
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            start_date = parse_start_date("start_date", default_start)
            if view == "fundamental":
                selected_ticker = request.form.get("ticker", "").strip().upper()
                if selected_ticker not in selected_portfolio.values():
                    raise ValueError("El activo no pertenece al portafolio seleccionado.")
                try:
                    info = fetch_fundamental_information(selected_ticker)
                except (requests.RequestException, TimeoutError, YFException) as error:
                    logger.exception("Yahoo Finance fundamental lookup failed for %s", selected_ticker)
                    raise ValueError(
                        f"No se pudo consultar Yahoo Finance para {selected_ticker}: {error}"
                    ) from error
                if not info:
                    flash(
                        f"Yahoo Finance no devolvió información para {selected_ticker}.",
                        "warning",
                    )
                else:
                    currency = str(info.get("currency") or "USD")
                    indicators = []
                    for label, key, kind in _FUNDAMENTAL_METRICS:
                        value = info.get(key)
                        if key == "currentPrice" and value is None:
                            value = info.get("regularMarketPrice")
                        indicators.append(
                            {
                                "label": label,
                                "value": _format_fundamental_value(
                                    value, kind, currency
                                ),
                            }
                        )
                    fundamentals = pd.DataFrame(
                        [
                            {
                                "Indicador": label,
                                "Valor": _format_fundamental_value(
                                    info.get(key), kind, currency
                                ),
                            }
                            for label, key, kind in _FUNDAMENTAL_ROWS
                        ]
                    )
                    result = {
                        "fundamental": {
                            "ticker": selected_ticker,
                            "name": info.get("longName")
                            or info.get("shortName")
                            or selected_ticker,
                            "sector": info.get("sector") or "Sector no disponible",
                            "industry": info.get("industry") or "Industria no disponible",
                            "country": info.get("country") or "País no disponible",
                            "summary": info.get("longBusinessSummary"),
                            "indicators": indicators,
                            "annual_range": (
                                f"{_format_fundamental_value(info.get('fiftyTwoWeekLow'), 'currency', currency)} – "
                                f"{_format_fundamental_value(info.get('fiftyTwoWeekHigh'), 'currency', currency)}"
                                if info.get("fiftyTwoWeekLow") is not None
                                and info.get("fiftyTwoWeekHigh") is not None
                                else "N/D"
                            ),
                            "table": _html_table(fundamentals, index=False),
                        }
                    }
            else:
                selected = selected_tickers()
                if view == "options":
                    valid_tickers = set(ACTIVOS_OPCIONES.values())
                    tickers = [
                        ticker for ticker in selected if ticker in valid_tickers
                    ]
                    tickers.extend(
                        ticker
                        for ticker in selected
                        if ticker not in valid_tickers
                    )
                else:
                    tickers = [
                        ticker
                        for ticker in selected
                        if ticker in selected_portfolio.values()
                        or ticker not in set().union(
                            *(set(p.values()) for p in PORTAFOLIOS_MONITOREO.values())
                        )
                    ]
                tickers = list(dict.fromkeys(tickers))
                if not tickers:
                    raise ValueError("Selecciona al menos un activo para monitorear.")
                try:
                    prices = download_prices(tuple(tickers), start_date, today)
                except (requests.RequestException, TimeoutError, YFException) as error:
                    logger.exception(
                        "Yahoo Finance request failed during asset monitoring"
                    )
                    raise ValueError(
                        f"No se pudieron descargar los precios: {error}"
                    ) from error
                prices = prices.dropna(axis="columns", how="all").ffill().dropna()
                if prices.empty or prices.shape[1] == 0:
                    raise ValueError(
                        "Yahoo Finance no devolvió precios para los activos y fechas seleccionados."
                    )
                missing = sorted(set(tickers) - set(prices.columns))
                if missing:
                    flash(
                        "No se recibieron datos para: " + ", ".join(missing),
                        "warning",
                    )
                returns = prices.pct_change(fill_method=None).dropna(how="all")
                stats = calculate_statistics(prices)
                expected = calculate_expected_returns(prices)
                charts = {
                    "prices": _monitoring_chart(
                        prices, "Precios ajustados", "Precio de cierre"
                    ),
                    "returns": _monitoring_chart(
                        returns, "Retornos históricos", "Retorno diario"
                    ),
                }
                tables = {
                    "prices": _html_table(
                        prices.tail(60),
                        {ticker: lambda value: f"{value:,.2f}" for ticker in prices},
                    ),
                    "returns": _html_table(
                        returns.tail(60),
                        {ticker: lambda value: f"{value:.2%}" for ticker in returns},
                    ),
                    "expected": _html_table(
                        expected,
                        {
                            column: lambda value: f"{value:.2%}"
                            for column in expected.columns
                        },
                    ),
                    "statistics": _html_table(
                        stats,
                        {
                            "Último precio": lambda value: f"{value:,.2f}",
                            "Retorno total": lambda value: f"{value:.2%}",
                            "Retorno anualizado": lambda value: f"{value:.2%}",
                            "Volatilidad anualizada": lambda value: f"{value:.2%}",
                            "Máxima caída": lambda value: f"{value:.2%}",
                        },
                    ),
                }
                if view == "options":
                    period = selected_option(
                        "period", PERIODOS_VOLATILIDAD, "Mensual"
                    )
                    period_vol, annualized_vol = calculate_historical_volatility(
                        returns, period, prices.index.min(), prices.index.max()
                    )
                    if not annualized_vol.empty:
                        charts["volatility"] = _volatility_histograms(
                            annualized_vol,
                            period_vol,
                            period,
                            list(annualized_vol.columns),
                        )
                        latest = pd.DataFrame(
                            {
                                f"VH {period.lower()}": period_vol.iloc[-1],
                                "VH anualizada": annualized_vol.iloc[-1],
                            }
                        )
                        tables["volatility"] = _html_table(
                            latest,
                            {
                                column: lambda value: f"{value:.2%}"
                                for column in latest.columns
                            },
                        )
                    else:
                        flash(
                            "No hay periodos completos con suficientes datos para la volatilidad.",
                            "warning",
                        )
                else:
                    monthly_vol = calculate_monthly_volatility(returns)
                    current_vol = returns.tail(21).std() * np.sqrt(21)
                    if not monthly_vol.empty:
                        selected_volatility_ticker = request.form.get(
                            "volatility_ticker", monthly_vol.columns[0]
                        )
                        if selected_volatility_ticker not in monthly_vol.columns:
                            selected_volatility_ticker = monthly_vol.columns[0]
                        charts["volatility"] = _single_histogram(
                            monthly_vol[selected_volatility_ticker],
                            selected_volatility_ticker,
                            current_vol.get(selected_volatility_ticker, np.nan),
                        )
                    tables["volatility"] = _html_table(
                        current_vol.rename("Volatilidad mensual actual").to_frame(),
                        {
                            "Volatilidad mensual actual": lambda value: f"{value:.2%}"
                        },
                    )
                result = {
                    "charts": charts,
                    "tables": tables,
                    "tickers": list(prices.columns),
                    "missing": missing,
                    "start": prices.index.min().date().isoformat(),
                    "end": prices.index.max().date().isoformat(),
                    "observations": len(prices),
                    "volatility_tickers": list(monthly_vol.columns)
                    if view == "portfolio" and not monthly_vol.empty
                    else [],
                    "selected_volatility_ticker": selected_volatility_ticker,
                }
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    return render_template(
        "monitoring/monitoring.html",
        view=view,
        today=today.isoformat(),
        default_start=start_value,
        portfolio_names=portfolio_names,
        portfolio_name=portfolio_name,
        portfolio_tickers=portfolio_tickers,
        option_tickers=list(ACTIVOS_OPCIONES.values()),
        period_names=list(PERIODOS_VOLATILIDAD),
        period=period,
        selected_ticker=selected_ticker,
        selected_volatility_ticker=selected_volatility_ticker,
        result=result,
    )
