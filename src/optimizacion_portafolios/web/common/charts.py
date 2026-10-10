"""Server-side Matplotlib charts rendered as PNG data URIs."""

import base64
from io import BytesIO

import matplotlib.pyplot as plt
import pandas as pd
import quantstats as qs

PRIMARY_COLOR = "#287d6b"
ACCENT_COLOR = "#d26045"


def figure_to_data_uri(figure) -> str:
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    plt.close(figure)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def line_chart(data: pd.Series | pd.DataFrame, title: str, ylabel: str) -> str:
    figure, axis = plt.subplots(figsize=(10, 3.8))
    data.plot(ax=axis, linewidth=1.25)
    axis.set_title(title, loc="left", fontweight="bold")
    axis.set_ylabel(ylabel)
    axis.set_xlabel("")
    axis.grid(axis="y", alpha=0.2)
    axis.spines[["top", "right"]].set_visible(False)
    if isinstance(data, pd.DataFrame):
        axis.legend(frameon=False, ncol=min(4, len(data.columns)))
    figure.tight_layout()
    return figure_to_data_uri(figure)


def bar_chart(data: pd.Series, title: str, ylabel: str) -> str:
    figure, axis = plt.subplots(figsize=(9, max(3.5, len(data) * 0.32)))
    data.sort_values().plot.barh(ax=axis, color=PRIMARY_COLOR)
    axis.set_title(title, loc="left", fontweight="bold")
    axis.set_xlabel(ylabel)
    axis.set_ylabel("")
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    return figure_to_data_uri(figure)


def correlation_chart(correlation: pd.DataFrame) -> str:
    figure, axis = plt.subplots(figsize=(max(6, len(correlation) * 0.55), 5.5))
    image = axis.imshow(correlation, cmap="RdYlGn", vmin=-1, vmax=1)
    labels = list(correlation.columns)
    axis.set_xticks(range(len(labels)), labels=labels, rotation=45, ha="right")
    axis.set_yticks(range(len(labels)), labels=labels)
    if len(labels) <= 18:
        for row in range(len(labels)):
            for column in range(len(labels)):
                axis.text(
                    column,
                    row,
                    f"{correlation.iloc[row, column]:.2f}",
                    ha="center",
                    va="center",
                    color="#172c29",
                    fontsize=8,
                )
    figure.colorbar(image, ax=axis, shrink=0.82, label="Correlación")
    axis.set_title("Correlación cuasi-diagonal", loc="left", fontweight="bold")
    figure.tight_layout()
    return figure_to_data_uri(figure)


def volatility_histogram(volatility: pd.Series, ticker: str, current: float) -> str:
    """Distribution of monthly volatility for one asset, marking the current value."""
    figure, axis = plt.subplots(figsize=(9, 3.5))
    axis.hist(volatility.dropna() * 100, bins="auto", color=PRIMARY_COLOR, edgecolor="white")
    if pd.notna(current):
        axis.axvline(
            current * 100,
            color=ACCENT_COLOR,
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


def monthly_volatility_histograms(monthly_volatility: pd.DataFrame) -> str:
    """One monthly-volatility histogram per asset, two per row."""
    columns = 2
    rows = (len(monthly_volatility.columns) + columns - 1) // columns
    figure, axes = plt.subplots(rows, columns, figsize=(12, 3.3 * rows), squeeze=False)
    for axis, ticker in zip(axes.flat, monthly_volatility.columns):
        values = monthly_volatility[ticker].dropna() * 100
        axis.hist(values, bins="auto", color=PRIMARY_COLOR, edgecolor="white")
        axis.axvline(values.iloc[-1], color=ACCENT_COLOR, linestyle="--")
        axis.set_title(f"{ticker} · actual {values.iloc[-1]:.2f}%", loc="left")
        axis.set_xlabel("Volatilidad mensual (%)")
        axis.set_ylabel("Meses")
        axis.grid(axis="y", alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
    for axis in list(axes.flat)[len(monthly_volatility.columns) :]:
        axis.set_visible(False)
    figure.tight_layout()
    return figure_to_data_uri(figure)


def period_volatility_histograms(
    annualized_volatility: pd.DataFrame,
    period_volatility: pd.DataFrame,
    period_label: str,
) -> str:
    """Annualized-volatility histograms per asset, annotated with the latest period."""
    available = [
        ticker
        for ticker in annualized_volatility.columns
        if annualized_volatility[ticker].notna().any()
    ]
    columns = 3
    rows = (len(available) + columns - 1) // columns
    figure, axes = plt.subplots(rows, columns, figsize=(15, 3.2 * rows), squeeze=False)
    for axis, ticker in zip(axes.flat, available):
        values = annualized_volatility[ticker].dropna() * 100
        latest_period = period_volatility.loc[values.index[-1], ticker]
        axis.hist(values, bins="auto", color=PRIMARY_COLOR, edgecolor="white")
        axis.axvline(values.iloc[-1], color=ACCENT_COLOR, linestyle="--", linewidth=1.5)
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


def quantstats_charts(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    rolling_period: int = 126,
) -> tuple[dict[str, str], bool]:
    """Render the QuantStats charts; rolling Sharpe only when history allows it."""
    charts = {}
    plots = (
        (
            "Rendimiento acumulado",
            lambda: qs.plots.returns(
                portfolio_returns,
                benchmark=benchmark_returns,
                figsize=(10, 5),
                show=False,
            ),
        ),
        (
            "Drawdown",
            lambda: qs.plots.drawdown(portfolio_returns, figsize=(10, 4), show=False),
        ),
        (
            "Rendimientos mensuales",
            lambda: qs.plots.monthly_heatmap(
                portfolio_returns,
                benchmark=benchmark_returns,
                figsize=(10, 5),
                show=False,
            ),
        ),
    )
    for title, create_plot in plots:
        charts[title] = figure_to_data_uri(create_plot())

    has_rolling_sharpe = len(portfolio_returns) >= rolling_period
    if has_rolling_sharpe:
        figure = qs.plots.rolling_sharpe(
            portfolio_returns,
            benchmark=benchmark_returns,
            period=rolling_period,
            figsize=(10, 4),
            show=False,
        )
        charts["Sharpe móvil"] = figure_to_data_uri(figure)
    return charts, has_rolling_sharpe
