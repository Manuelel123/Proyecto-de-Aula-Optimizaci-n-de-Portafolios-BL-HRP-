"""Legacy Matplotlib charts (PNG data URIs). Being replaced by charts.py (Plotly); delete once no page imports it."""

import base64
from io import BytesIO

import matplotlib.pyplot as plt
import pandas as pd
import quantstats as qs
from cycler import cycler
from matplotlib.colors import LinearSegmentedColormap

# Brand palette shared with static/css/app.css: navy for data, gold for highlights.
PRIMARY_COLOR = "#1d3a63"
ACCENT_COLOR = "#c19a5b"
INK_COLOR = "#0f1b2d"
MUTED_COLOR = "#6b778a"
GRID_COLOR = "#e3e7ee"
SERIES_COLORS = (
    "#1d3a63",
    "#c19a5b",
    "#2f7d6d",
    "#8c3b4a",
    "#5b7fb5",
    "#7a6a55",
    "#4c5b70",
    "#d4a373",
    "#3e8e9e",
    "#9a7fb8",
)
CORRELATION_CMAP = LinearSegmentedColormap.from_list(
    "atlas_diverging", ["#8c3b4a", "#f7f6f2", "#1d3a63"]
)

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": GRID_COLOR,
        "axes.labelcolor": MUTED_COLOR,
        "axes.titlecolor": INK_COLOR,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlepad": 12,
        "axes.labelsize": 10,
        "axes.prop_cycle": cycler(color=SERIES_COLORS),
        "axes.grid": False,
        "grid.color": GRID_COLOR,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED_COLOR,
        "ytick.color": MUTED_COLOR,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "legend.labelcolor": INK_COLOR,
    }
)


def figure_to_data_uri(figure) -> str:
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=144, bbox_inches="tight")
    plt.close(figure)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def line_chart(data: pd.Series | pd.DataFrame, title: str, ylabel: str) -> str:
    figure, axis = plt.subplots(figsize=(10, 3.8))
    data.plot(ax=axis, linewidth=1.4)
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
    ordered = data.sort_values()
    ordered.plot.barh(ax=axis, color=PRIMARY_COLOR, width=0.68)
    if len(ordered):
        axis.patches[-1].set_color(ACCENT_COLOR)
    axis.set_title(title, loc="left", fontweight="bold")
    axis.set_xlabel(ylabel)
    axis.set_ylabel("")
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    return figure_to_data_uri(figure)


def correlation_chart(correlation: pd.DataFrame) -> str:
    figure, axis = plt.subplots(figsize=(max(6, len(correlation) * 0.55), 5.5))
    image = axis.imshow(correlation, cmap=CORRELATION_CMAP, vmin=-1, vmax=1)
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
                    color="white" if abs(correlation.iloc[row, column]) > 0.6 else INK_COLOR,
                    fontsize=8,
                )
    colorbar = figure.colorbar(image, ax=axis, shrink=0.82, label="Correlación")
    colorbar.outline.set_visible(False)
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
            linewidth=1.8,
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
        axis.axvline(values.iloc[-1], color=ACCENT_COLOR, linestyle="--", linewidth=1.8)
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
        axis.axvline(values.iloc[-1], color=ACCENT_COLOR, linestyle="--", linewidth=1.8)
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
            color=INK_COLOR,
            bbox={"boxstyle": "round", "facecolor": "white", "edgecolor": GRID_COLOR, "alpha": 0.9},
        )
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", alpha=0.18)
    for axis in list(axes.flat)[len(available) :]:
        axis.set_visible(False)
    figure.suptitle(
        f"Volatilidad histórica · {period_label}",
        x=0.02,
        ha="left",
        color=INK_COLOR,
        fontweight="bold",
    )
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
