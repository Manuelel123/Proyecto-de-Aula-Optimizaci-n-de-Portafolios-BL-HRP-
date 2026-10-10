"""Plotly figures serialized as JSON for client-side rendering.

Contract shared by every page (do not change names, signatures or return types):
each public chart function returns ``Markup`` with the figure JSON (safe to embed
in ``<script type="application/json">``) or ``None`` when there is not enough
data. Pages render them with ``macros/charts.html::plotly_chart``. Theme-dependent
colors (backgrounds, text, grid) are applied in the browser by static/js/charts.js
from CSS variables, so figures keep transparent backgrounds.

NOTE: the bodies below are minimal placeholders created to freeze the contract;
the charts module replaces them with the full designs.
"""

import pandas as pd
import plotly.graph_objects as go
from markupsafe import Markup

ROLLING_SHARPE_PERIOD = 126


def figure_payload(figure: go.Figure) -> Markup:
    """Serialize a figure to JSON escaped for an inline <script> block."""
    text = figure.to_json(validate=False, remove_uids=True)
    return Markup(
        text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def _bar(values: pd.Series, orientation: str = "h") -> Markup | None:
    values = values.dropna()
    if values.empty:
        return None
    if orientation == "h":
        trace = go.Bar(x=values.to_numpy(), y=[str(i) for i in values.index], orientation="h")
    else:
        trace = go.Bar(x=[str(i) for i in values.index], y=values.to_numpy())
    return figure_payload(go.Figure(trace))


def allocation_chart(
    weights: pd.Series,
    names: dict[str, str] | None = None,
    *,
    reference: float | None = None,
    market_weights: pd.Series | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> Markup | None:
    """Horizontal bars of the optimal weights (supports negative weights).

    ``reference``: vertical guide (e.g. 1/N for HRP). ``market_weights``: marker
    per asset with the market-cap weight (Black-Litterman). ``bounds``: per-asset
    (min, max) applied limits (HRP).
    """
    return _bar(weights.sort_values(ascending=False))


def cumulative_returns_chart(portfolio: pd.Series, benchmark: pd.Series) -> Markup | None:
    if portfolio.dropna().empty:
        return None
    figure = go.Figure()
    for series in (portfolio, benchmark):
        cumulative = (1 + series.dropna()).cumprod() - 1
        figure.add_trace(go.Scatter(x=cumulative.index, y=cumulative.to_numpy(), name=str(series.name)))
    return figure_payload(figure)


def drawdown_chart(portfolio: pd.Series) -> Markup | None:
    wealth = (1 + portfolio.dropna()).cumprod()
    if wealth.empty:
        return None
    drawdown = wealth / wealth.cummax() - 1
    return figure_payload(go.Figure(go.Scatter(x=drawdown.index, y=drawdown.to_numpy(), fill="tozeroy")))


def monthly_returns_heatmap(portfolio: pd.Series) -> Markup | None:
    monthly = (1 + portfolio.dropna()).resample("ME").prod() - 1
    if monthly.empty:
        return None
    table = monthly.groupby([monthly.index.year, monthly.index.month]).first().unstack()
    return figure_payload(
        go.Figure(go.Heatmap(z=table.to_numpy(), x=list(table.columns), y=[str(y) for y in table.index], zmid=0))
    )


def rolling_sharpe_chart(
    portfolio: pd.Series, benchmark: pd.Series, period: int = ROLLING_SHARPE_PERIOD
) -> Markup | None:
    if len(portfolio.dropna()) < period:
        return None
    figure = go.Figure()
    for series in (portfolio, benchmark):
        rolling = series.rolling(period)
        sharpe = (rolling.mean() / rolling.std() * 252**0.5).dropna()
        figure.add_trace(go.Scatter(x=sharpe.index, y=sharpe.to_numpy(), name=str(series.name)))
    return figure_payload(figure)


def returns_distribution_chart(portfolio: pd.Series, benchmark: pd.Series) -> Markup | None:
    if portfolio.dropna().empty:
        return None
    figure = go.Figure()
    for series in (portfolio, benchmark):
        monthly = (1 + series.dropna()).resample("ME").prod() - 1
        figure.add_trace(go.Histogram(x=monthly.to_numpy(), name=str(series.name), opacity=0.7))
    return figure_payload(figure)


def yearly_returns_chart(portfolio: pd.Series, benchmark: pd.Series) -> Markup | None:
    if portfolio.dropna().empty:
        return None
    figure = go.Figure()
    for series in (portfolio, benchmark):
        yearly = (1 + series.dropna()).groupby(series.dropna().index.year).prod() - 1
        figure.add_trace(go.Bar(x=[str(y) for y in yearly.index], y=yearly.to_numpy(), name=str(series.name)))
    return figure_payload(figure)


def historical_charts(
    portfolio: pd.Series,
    benchmark: pd.Series,
    rolling_period: int = ROLLING_SHARPE_PERIOD,
) -> tuple[dict[str, Markup], bool]:
    """Charts for the "Análisis histórico vs benchmark" section.

    Keys: cumulative, drawdown, monthly_heatmap, rolling_sharpe (only when
    there is enough history), returns_distribution, yearly_returns. The
    boolean says whether the rolling Sharpe chart could be built.
    """
    charts = {
        "cumulative": cumulative_returns_chart(portfolio, benchmark),
        "drawdown": drawdown_chart(portfolio),
        "monthly_heatmap": monthly_returns_heatmap(portfolio),
        "rolling_sharpe": rolling_sharpe_chart(portfolio, benchmark, rolling_period),
        "returns_distribution": returns_distribution_chart(portfolio, benchmark),
        "yearly_returns": yearly_returns_chart(portfolio, benchmark),
    }
    has_rolling_sharpe = charts["rolling_sharpe"] is not None
    return {key: value for key, value in charts.items() if value is not None}, has_rolling_sharpe


def correlation_heatmap(matrix: pd.DataFrame, title: str = "Correlación") -> Markup | None:
    if matrix.empty:
        return None
    labels = [str(label) for label in matrix.columns]
    return figure_payload(
        go.Figure(go.Heatmap(z=matrix.to_numpy(), x=labels, y=labels, zmin=-1, zmid=0, zmax=1))
    )


def dendrogram_chart(clustering) -> Markup | None:
    """Dendrogram of a fitted skfolio HierarchicalClustering estimator."""
    if clustering is None:
        return None
    return figure_payload(clustering.plot_dendrogram(heatmap=False))


def contribution_chart(frame: pd.DataFrame, column: str) -> Markup | None:
    if column not in frame:
        return None
    return _bar(frame[column].sort_values(ascending=False))


def volatility_histogram_chart(
    monthly_volatility: pd.DataFrame, current: pd.Series | None = None
) -> Markup | None:
    """Monthly-volatility histogram per asset, one visible at a time (selector)."""
    if monthly_volatility.dropna(how="all").empty:
        return None
    figure = go.Figure()
    for index, ticker in enumerate(monthly_volatility.columns):
        figure.add_trace(
            go.Histogram(x=monthly_volatility[ticker].dropna().to_numpy(), name=str(ticker), visible=index == 0)
        )
    return figure_payload(figure)


def returns_box_chart(returns: pd.DataFrame) -> Markup | None:
    if returns.dropna(how="all").empty:
        return None
    figure = go.Figure()
    for ticker in returns.columns:
        figure.add_trace(go.Box(y=returns[ticker].dropna().to_numpy(), name=str(ticker)))
    return figure_payload(figure)


def prior_posterior_chart(prior: pd.Series, views: pd.Series, posterior: pd.Series) -> Markup | None:
    if posterior.dropna().empty:
        return None
    figure = go.Figure()
    for name, series in (("Prior", prior), ("View", views), ("Posterior", posterior)):
        figure.add_trace(go.Bar(x=[str(i) for i in series.index], y=series.to_numpy(), name=name))
    return figure_payload(figure)


def period_volatility_chart(
    annualized: pd.DataFrame, period_volatility: pd.DataFrame, label: str
) -> Markup | None:
    """Latest annualized volatility per asset (bars) for the options view."""
    if annualized.dropna(how="all").empty:
        return None
    return _bar(annualized.iloc[-1].sort_values(ascending=False))
