"""Reusable plots and safely rendered tables for portfolio pages."""

import matplotlib
import pandas as pd
import quantstats as qs

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from optimizacion_portafolios.web.charts import figure_to_data_uri


def dataframe_html(
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


def metrics_html(metrics: pd.DataFrame) -> str:
    percentage_metrics = {
        "Retorno anual compuesto",
        "Volatilidad anualizada",
        "Máxima caída",
        "Porcentaje de días positivos",
    }
    rows = [
        {
            "Indicador": name,
            "Valor": (
                "N/D"
                if pd.isna(value)
                else (
                    f"{value:.2%}"
                    if name in percentage_metrics
                    else f"{value:.3f}"
                )
            ),
        }
        for name, value in metrics["Valor"].items()
    ]
    return dataframe_html(pd.DataFrame(rows), index=False)


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
    data.sort_values().plot.barh(ax=axis, color="#287d6b")
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


def quantstats_charts(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    rolling_period: int = 126,
) -> tuple[dict[str, str], bool]:
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
