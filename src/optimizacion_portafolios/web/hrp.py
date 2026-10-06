"""Hierarchical risk parity workflow."""

import logging
from datetime import date
from io import BytesIO

import matplotlib
import numpy as np
import pandas as pd
import requests
from flask import Blueprint, flash, render_template, request, send_file
from yfinance.exceptions import YFException

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from optimizacion_portafolios.analytics import (
    calculate_hrp,
    calculate_hrp_contributions,
    calculate_monthly_volatility,
    calculate_quantstats_metrics,
    date_one_year_ago,
    generate_tearsheet,
)
from optimizacion_portafolios.catalogs import (
    ACTIVOS_HRP,
    BENCHMARKS,
    UNIVERSOS_HRP,
)
from optimizacion_portafolios.market_data import download_prices
from optimizacion_portafolios.web.forms import (
    normalize_ticker,
    parse_start_date,
    selected_option,
    selected_tickers,
)
from optimizacion_portafolios.web.charts import figure_to_data_uri
from optimizacion_portafolios.web.portfolio_views import (
    bar_chart,
    correlation_chart,
    dataframe_html,
    line_chart,
    metrics_html,
    quantstats_charts,
)

logger = logging.getLogger(__name__)
hrp_blueprint = Blueprint("hrp", __name__)
_ROLLING_SHARPE_PERIOD = 126


def _format_percent(value) -> str:
    return "N/D" if pd.isna(value) else f"{value:.2%}"


def _run_hrp(
    tickers: list[str],
    benchmark: str,
    start_date: date,
    download_report: bool = False,
):
    today = date.today()
    prices = download_prices(tuple(tickers), start_date, today)
    prices = prices.dropna(axis="columns", how="all").ffill().dropna()
    if prices.shape[1] < 2:
        raise ValueError(
            "Yahoo Finance devolvió datos para menos de dos activos seleccionados."
        )
    missing = sorted(set(tickers) - set(prices.columns))
    returns = prices.pct_change(fill_method=None).dropna(how="all").dropna()
    if len(returns) < 2:
        raise ValueError("Se necesitan más observaciones para optimizar HRP.")

    weights, ordered_correlation = calculate_hrp(returns)
    benchmark_prices = download_prices((benchmark,), start_date, today)
    if benchmark_prices.empty or benchmark not in benchmark_prices:
        raise ValueError(
            f"Yahoo Finance no devolvió datos para el benchmark {benchmark}."
        )
    benchmark_returns = benchmark_prices[benchmark].pct_change(
        fill_method=None
    ).rename(benchmark)
    portfolio_returns = returns.dot(weights).rename("Portafolio HRP")
    aligned = pd.concat(
        [portfolio_returns, benchmark_returns], axis=1, sort=False
    ).dropna()
    if aligned.empty:
        raise ValueError(
            f"No hay retornos coincidentes entre el portafolio y el benchmark {benchmark}."
        )
    portfolio_returns = aligned["Portafolio HRP"]
    benchmark_returns = aligned[benchmark]
    if download_report:
        return {
            "report": generate_tearsheet(
                portfolio_returns,
                benchmark_returns,
                "Portafolio HRP",
                "reporte_quantstats_hrp.html",
            ),
            "missing": missing,
        }

    names = {ticker: ticker for ticker in ACTIVOS_HRP.values()}
    weights_frame = pd.DataFrame(
        {
            "Activo": [names.get(ticker, ticker) for ticker in weights.index],
            "Ticker": weights.index,
            "Peso": weights.values,
        }
    )
    _, contributions = calculate_hrp_contributions(returns, weights)
    contributions = contributions.rename(index=names).rename_axis("Activo")
    metrics = calculate_quantstats_metrics(portfolio_returns)
    charts, has_rolling_sharpe = quantstats_charts(
        portfolio_returns,
        benchmark_returns,
        rolling_period=_ROLLING_SHARPE_PERIOD,
    )
    monthly_volatility = calculate_monthly_volatility(returns)
    volatility_figure = None
    if not monthly_volatility.empty:
        columns = 2
        rows = (len(monthly_volatility.columns) + columns - 1) // columns
        figure, axes = plt.subplots(
            rows, columns, figsize=(12, 3.3 * rows), squeeze=False
        )
        for axis, ticker in zip(axes.flat, monthly_volatility.columns):
            values = monthly_volatility[ticker].dropna() * 100
            axis.hist(values, bins="auto", color="#287d6b", edgecolor="white")
            axis.axvline(values.iloc[-1], color="#d26045", linestyle="--")
            axis.set_title(f"{ticker} · actual {values.iloc[-1]:.2f}%", loc="left")
            axis.set_xlabel("Volatilidad mensual (%)")
            axis.set_ylabel("Meses")
            axis.grid(axis="y", alpha=0.2)
            axis.spines[["top", "right"]].set_visible(False)
        for axis in list(axes.flat)[len(monthly_volatility.columns) :]:
            axis.set_visible(False)
        figure.tight_layout()
        volatility_figure = figure_to_data_uri(figure)

    weights_table = dataframe_html(
        weights_frame,
        {"Peso": _format_percent},
        index=False,
    )
    contributions_table = dataframe_html(
        contributions,
        {
            column: _format_percent
            for column in (
                "Peso HRP",
                "Retorno anualizado",
                "Volatilidad anualizada",
                "Contribución anualizada",
                "Participación del retorno",
            )
        },
    )
    metrics_table = metrics_html(metrics)
    correlation_table = dataframe_html(
        ordered_correlation,
        {column: lambda value: f"{value:.2f}" for column in ordered_correlation},
    )
    charts.update(
        {
            "Evolución de precios": line_chart(
                prices, "Precios ajustados", "Precio de cierre"
            ),
            "Retornos por activo": line_chart(
                returns, "Retornos históricos por activo", "Retorno diario"
            ),
            "Retorno del portafolio": line_chart(
                portfolio_returns,
                "Retornos diarios del portafolio HRP",
                "Retorno diario",
            ),
            "Evolución unitaria": line_chart(
                prices.div(prices.iloc[0]), "Rendimiento relativo · inicio = 1", "Índice"
            ),
            "Correlación": correlation_chart(ordered_correlation),
            "Pesos": bar_chart(weights, "Asignación por activo", "Peso"),
            "Volatilidad mensual": volatility_figure,
        }
    )
    return {
        "charts": charts,
        "tables": {
            "weights": weights_table,
            "contributions": contributions_table,
            "metrics": metrics_table,
            "correlation": correlation_table,
        },
        "start": prices.index.min().date().isoformat(),
        "end": prices.index.max().date().isoformat(),
        "observations": len(returns),
        "common_observations": len(aligned),
        "weight_sum": float(weights.sum()),
        "missing": missing,
        "has_rolling_sharpe": has_rolling_sharpe,
        "selected_tickers": list(prices.columns),
        "returns": portfolio_returns,
        "benchmark_returns": benchmark_returns,
        "benchmark": benchmark,
    }


@hrp_blueprint.route("/hrp", methods=["GET", "POST"])
def hrp():
    today = date.today()
    default_start = date_one_year_ago(today)
    universe_names = list(UNIVERSOS_HRP)
    universe_name = request.values.get("universe", "Portafolio actual")
    if universe_name not in UNIVERSOS_HRP:
        flash("Selecciona un universo de activos válido.", "error")
        universe_name = "Portafolio actual"
    universe = UNIVERSOS_HRP[universe_name]
    available_tickers = list(universe.values())
    selected = request.form.getlist("tickers") if request.method == "POST" else []
    if request.method == "POST" and request.form.get("action") == "refresh":
        selected = [ticker for ticker in selected if ticker in available_tickers]
        if not selected:
            selected = available_tickers
    elif not selected:
        selected = available_tickers

    benchmark_names = list(BENCHMARKS)
    benchmark_name = request.values.get("benchmark", benchmark_names[0])
    if benchmark_name not in BENCHMARKS:
        benchmark_name = benchmark_names[0]
    custom_benchmark = request.values.get("custom_benchmark", "").strip()
    benchmark_ticker = BENCHMARKS[benchmark_name]
    start_value = request.values.get("start_date", default_start.isoformat())
    result = None

    if request.method == "POST" and request.form.get("action") != "refresh":
        try:
            selected = selected_tickers()
            custom = {
                ticker.strip().upper()
                for ticker in request.form.get("custom_tickers", "").split(",")
                if ticker.strip()
            }
            allowed = set(ACTIVOS_HRP.values()) | custom
            invalid = [ticker for ticker in selected if ticker not in allowed]
            if invalid:
                raise ValueError("Activos no disponibles: " + ", ".join(invalid))
            if len(selected) < 2:
                raise ValueError("Selecciona al menos dos activos para calcular HRP.")
            benchmark_name = selected_option(
                "benchmark", BENCHMARKS, benchmark_names[0]
            )
            custom_benchmark = request.form.get("custom_benchmark", "").strip()
            benchmark_ticker = (
                normalize_ticker(custom_benchmark)
                if custom_benchmark
                else BENCHMARKS[benchmark_name]
            )
            start_date = parse_start_date("start_date", default_start)
            try:
                download_requested = (
                    request.form.get("action") == "download_report"
                )
                result = _run_hrp(
                    selected,
                    benchmark_ticker,
                    start_date,
                    download_report=download_requested,
                )
            except (requests.RequestException, TimeoutError, YFException) as error:
                logger.exception("Yahoo Finance request failed during HRP optimization")
                raise ValueError(f"No se pudieron descargar los datos: {error}") from error
            if result["missing"]:
                flash(
                    "Yahoo Finance no devolvió datos para: "
                    + ", ".join(result["missing"]),
                    "warning",
                )
            if download_requested:
                return send_file(
                    BytesIO(result["report"]),
                    mimetype="text/html",
                    as_attachment=True,
                    download_name="reporte_quantstats_hrp.html",
                )
            if not result["has_rolling_sharpe"]:
                flash(
                    "El Sharpe móvil requiere al menos 126 retornos alineados; "
                    "los demás resultados HRP están disponibles.",
                    "warning",
                )
        except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError) as error:
            flash(str(error), "error")

    return render_template(
        "hrp.html",
        today=today.isoformat(),
        default_start=start_value,
        universe_names=universe_names,
        universe_name=universe_name,
        available_tickers=available_tickers,
        selected_tickers=selected,
        benchmark_names=benchmark_names,
        benchmark_name=benchmark_name,
        custom_benchmark=custom_benchmark,
        result=result,
    )
