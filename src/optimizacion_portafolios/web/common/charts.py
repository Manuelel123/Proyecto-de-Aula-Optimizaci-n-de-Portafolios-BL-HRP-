"""Plotly figures serialized as JSON for client-side rendering.

Contract shared by every page (do not change names, signatures or return types):
each public chart function returns ``Markup`` with the figure JSON (safe to embed
in ``<script type="application/json">``) or ``None`` when there is not enough
data. Pages render them with ``macros/charts.html::plotly_chart``. Theme-dependent
colors (text, grid, series) are written as the light-theme token hex values of
``chart_theme`` and swapped in the browser by static/js/charts.js from CSS
variables, so figures keep transparent backgrounds and follow the dark theme.

Design rules shared by every figure: template "atlas" (never the default
"plotly" template), no title inside the figure (the title lives in the HTML
card), horizontal legend on top, point decimal separator (``separators=".,"``),
Spanish hover texts with exact values, categorical colors assigned by the order
in which the tickers are received (never by rank).
"""

from collections.abc import Iterable
from math import isfinite, sqrt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from markupsafe import Markup

from optimizacion_portafolios.web.common import chart_theme as theme

ROLLING_SHARPE_PERIOD = 126
TRADING_DAYS = 252
MONTHS = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
MONO_FONT = "JetBrains Mono, ui-monospace, monospace"
DATE_HOVER = "%d %b %Y"
BAR_ROW_HEIGHT = 28


def figure_payload(figure: go.Figure) -> Markup:
    """Serialize a figure to JSON escaped for an inline <script> block."""
    text = figure.to_json(validate=False, remove_uids=True)
    return Markup(
        text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


# --------------------------------------------------------------------------- helpers


def _figure(**layout) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(template=theme.ATLAS_TEMPLATE, separators=".,", **layout)
    return figure


def _clean(series: pd.Series | None) -> pd.Series:
    """Numeric values without NaN/inf (empty Series when there is nothing)."""
    if series is None:
        return pd.Series(dtype=float)
    values = pd.to_numeric(pd.Series(series), errors="coerce").astype(float)
    return values[np.isfinite(values.to_numpy())]


def _label(series: pd.Series | None, default: str) -> str:
    name = getattr(series, "name", None)
    return str(name) if name not in (None, "") else default


def _percent(value: float, decimals: int = 2) -> str:
    """Static percent text with point decimal and a real minus sign."""
    return f"{value:.{decimals}%}".replace("-", "−")


def _date(value) -> str:
    return pd.Timestamp(value).strftime("%d/%m/%Y")


def _finite_or_none(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _none_list(values: Iterable) -> list:
    """Plain list with None for missing values (heatmap gaps, no NaN)."""
    return [_finite_or_none(value) for value in values]


def _compound_monthly(returns: pd.Series | None) -> pd.Series:
    returns = _clean(returns)
    if returns.empty or not isinstance(returns.index, pd.DatetimeIndex):
        return pd.Series(dtype=float)
    grouped = (1 + returns).resample("ME")
    monthly = grouped.prod() - 1
    return monthly[grouped.count() > 0]


def _bar_height(count: int, extra: int = 0) -> int:
    return max(160, BAR_ROW_HEIGHT * count + 80 + extra)


def _padded_range(values: Iterable, pad: float = 0.14) -> list[float]:
    finite = [number for value in values if (number := _finite_or_none(value)) is not None]
    low = min([0.0, *finite])
    high = max([0.0, *finite])
    span = (high - low) or 1.0
    return [low - (span * pad if low < 0 else 0), high + (span * pad if high > 0 else 0)]


def _vertical_marker(value: float, text: str, xref: str = "x", yref: str = "paper") -> tuple[dict, dict]:
    """Dashed "current value" guide (text-1) plus its label at the top."""
    shape = {
        "type": "line",
        "xref": xref,
        "yref": yref,
        "x0": value,
        "x1": value,
        "y0": 0,
        "y1": 1,
        "line": {"color": theme.TEXT_1, "width": 1.5, "dash": "dash"},
    }
    annotation = {
        "xref": xref,
        "yref": yref,
        "x": value,
        "y": 1,
        "xanchor": "left",
        "yanchor": "top",
        "xshift": 4,
        "text": text,
        "showarrow": False,
        "font": {"size": 11, "color": theme.TEXT_1},
        "bgcolor": theme.SURFACE_1,
    }
    return shape, annotation


def _ticker_axis(**extra) -> dict:
    return {"type": "category", "tickfont": {"family": MONO_FONT, "size": 11}, **extra}


def _value_bar(y: list[str], x, *, name: str, colors, hovertemplate: str, **extra) -> go.Bar:
    """Horizontal bar with outside value labels in text ink (never series color)."""
    return go.Bar(
        y=y,
        x=x,
        orientation="h",
        name=name,
        marker={"color": colors},
        texttemplate="%{x:.1%}",
        textposition="outside",
        textfont={"size": 11, "color": theme.TEXT_1},
        cliponaxis=False,
        hovertemplate=hovertemplate,
        **extra,
    )


def _tick_marker(y: list[str], x, name: str) -> go.Scatter:
    """Vertical tick marker per asset (market weight, weight to compare)."""
    return go.Scatter(
        y=y,
        x=x,
        mode="markers",
        name=name,
        marker={
            "symbol": "line-ns-open",
            "size": 20,
            "color": theme.TEXT_1,
            "line": {"width": 2.5, "color": theme.TEXT_1},
        },
        hovertemplate=f"{name}: %{{x:.2%}}<extra></extra>",
    )


def _reindexed(series: pd.Series | None, tickers: list[str]) -> pd.Series:
    values = _clean(series)
    values.index = [str(ticker) for ticker in values.index]
    return values.reindex(tickers)


# --------------------------------------------------------------------------- allocation


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

    Bars sorted descending (largest on top), slot-1 color, negatives in the
    negative color with the axis anchored at 0, value labels outside, height
    proportional to the number of assets (``layout.height``).
    """
    weights = _clean(weights)
    if weights.empty:
        return None
    names = names or {}
    ordered = weights.sort_values(ascending=False)
    tickers = [str(ticker) for ticker in ordered.index]
    labels = [str(names.get(ticker, names.get(str(ticker), ticker))) for ticker in ordered.index]
    values = ordered.to_numpy(dtype=float)
    colors = [theme.NEGATIVE if value < 0 else theme.PRIMARY for value in values]

    extras = bool(bounds) or market_weights is not None or reference is not None
    figure = _figure(
        height=_bar_height(len(tickers), 28 if extras else 0),
        hovermode="y unified" if extras else "closest",
        barmode="overlay",
        showlegend=extras,
        margin={"l": 64, "r": 16, "t": 36 if extras else 12, "b": 36},
    )
    range_values = list(values)

    figure.add_trace(
        _value_bar(
            tickers,
            values,
            name="Peso óptimo",
            colors=colors,
            customdata=labels,
            width=0.5 if bounds else 0.66,
            hovertemplate=(
                "%{customdata} · Peso óptimo: %{x:.2%}<extra></extra>"
                if extras
                else "<b>%{y}</b> · %{customdata}<br>Peso: %{x:.2%}<extra></extra>"
            ),
        )
    )

    if bounds:
        # Faint min–max band per asset, drawn as shapes *below* the bars so the
        # weights bar remains the first (and only) bar trace of the figure.
        first = True
        for position, ticker in enumerate(tickers):
            limits = bounds.get(ticker)
            if limits is None:
                continue
            low, high = _finite_or_none(limits[0]), _finite_or_none(limits[1])
            if low is None or high is None:
                continue
            figure.add_shape(
                type="rect",
                xref="x",
                yref="y",
                x0=low,
                x1=high,
                y0=position - 0.42,
                y1=position + 0.42,
                fillcolor=theme.with_alpha(theme.TEXT_DISABLED, 0.24),
                line={"width": 0},
                layer="below",
                showlegend=first,
                legendgroup="bounds",
                name="Rango permitido (mín–máx)",
            )
            first = False
            range_values += [low, high]

    if market_weights is not None:
        market = _reindexed(market_weights, tickers).dropna()
        if not market.empty:
            figure.add_trace(
                _tick_marker(list(market.index), market.to_numpy(dtype=float), "Peso de mercado")
            )
            range_values += list(market.to_numpy(dtype=float))

    reference = _finite_or_none(reference)
    if reference is not None:
        figure.add_shape(
            type="line",
            xref="x",
            yref="paper",
            x0=reference,
            x1=reference,
            y0=0,
            y1=1,
            line={"color": theme.TEXT_3, "width": 1.5, "dash": "dot"},
            layer="above",
            showlegend=True,
            name=f"Peso equitativo ({_percent(reference)})",
        )
        range_values.append(reference)

    figure.update_xaxes(
        tickformat=".0%",
        hoverformat=".2%",
        showgrid=True,
        zeroline=True,
        range=_padded_range(range_values),
    )
    figure.update_yaxes(**_ticker_axis(autorange="reversed"))
    return figure_payload(figure)


# --------------------------------------------------------------------------- history


def _time_series_axes(figure: go.Figure, tickformat: str) -> None:
    figure.update_xaxes(type="date", hoverformat=DATE_HOVER, showspikes=True, showgrid=False)
    figure.update_yaxes(tickformat=tickformat, showgrid=True, zeroline=True)


def _iso_dates(index) -> list[str]:
    """Compact ISO dates (no time part) for daily series."""
    return [pd.Timestamp(day).strftime("%Y-%m-%d") for day in index]


def _line(x, y, name: str, color: str, width: float, dash: str, value_format: str) -> go.Scatter:
    return go.Scatter(
        x=_iso_dates(x),
        y=y,
        name=name,
        mode="lines",
        line={"color": color, "width": width, "dash": dash},
        hovertemplate=f"{name}: %{{y:{value_format}}}<extra></extra>",
    )


def cumulative_returns_chart(portfolio: pd.Series, benchmark: pd.Series) -> Markup | None:
    """Compounded cumulative return: portfolio (slot 1) vs benchmark (gray dotted)."""
    returns = _clean(portfolio)
    if returns.empty:
        return None
    figure = _figure(hovermode="x unified", showlegend=True, margin={"t": 32})
    series = [(returns, _label(portfolio, "Portafolio"), theme.PRIMARY, 2, "solid")]
    bench = _clean(benchmark)
    if not bench.empty:
        series.append((bench, _label(benchmark, "Benchmark"), theme.TEXT_3, 1.5, "dot"))
    for values, name, color, width, dash in series:
        cumulative = (1 + values).cumprod() - 1
        figure.add_trace(_line(cumulative.index, cumulative.to_numpy(), name, color, width, dash, ".2%"))
    _time_series_axes(figure, ".0%")
    return figure_payload(figure)


def drawdown_chart(portfolio: pd.Series) -> Markup | None:
    """Underwater area (drawdown from the running peak) with the worst drop marked."""
    returns = _clean(portfolio)
    if returns.empty:
        return None
    wealth = (1 + returns).cumprod()
    drawdown = wealth / wealth.cummax() - 1
    figure = _figure(hovermode="x unified", showlegend=False, margin={"t": 16})
    figure.add_trace(
        go.Scatter(
            x=_iso_dates(drawdown.index),
            y=drawdown.to_numpy(),
            name="Drawdown",
            mode="lines",
            fill="tozeroy",
            line={"color": theme.NEGATIVE, "width": 1.5},
            fillcolor=theme.with_alpha(theme.NEGATIVE, 0.12),
            hovertemplate="Caída desde el máximo: %{y:.2%}<extra></extra>",
        )
    )
    worst_date = drawdown.idxmin()
    worst = float(drawdown.min())
    if worst < 0:
        figure.add_trace(
            go.Scatter(
                x=_iso_dates([worst_date]),
                y=[worst],
                mode="markers",
                name="Peor caída",
                marker={"size": 9, "color": theme.NEGATIVE, "line": {"width": 2, "color": theme.SURFACE_1}},
                hoverinfo="skip",
            )
        )
        figure.add_annotation(
            x=_iso_dates([worst_date])[0],
            y=worst,
            text=f"Peor caída {_percent(worst)} · {_date(worst_date)}",
            showarrow=False,
            yanchor="top",
            yshift=-8,
            font={"size": 11, "color": theme.TEXT_1},
            bgcolor=theme.SURFACE_1,
        )
    _time_series_axes(figure, ".0%")
    figure.update_yaxes(range=[min(worst, -0.01) * 1.25, 0.004], zeroline=False)
    return figure_payload(figure)


def monthly_returns_heatmap(portfolio: pd.Series) -> Markup | None:
    """Year × month heatmap of compounded monthly returns plus a yearly column."""
    monthly = _compound_monthly(portfolio)
    if monthly.empty:
        return None
    table = (
        monthly.groupby([monthly.index.year, monthly.index.month])
        .first()
        .unstack()
        .reindex(columns=range(1, 13))
    )
    yearly = monthly.groupby(monthly.index.year).apply(lambda values: (1 + values).prod() - 1)
    years = [str(year) for year in table.index]
    month_bound = float(np.nanmax(np.abs(table.to_numpy(dtype=float)))) or 0.01
    year_bound = float(np.nanmax(np.abs(yearly.to_numpy(dtype=float)))) or 0.01
    common = {
        "y": years,
        "zmid": 0,
        "colorscale": theme.RETURNS_SCALE,
        "showscale": False,
        "xgap": 2,
        "ygap": 2,
        "hoverongaps": False,
        "texttemplate": "%{z:.1%}",
        "textfont": {"size": 11},
    }
    figure = _figure(
        height=max(180, 36 * len(years) + 80),
        showlegend=False,
        margin={"l": 48, "r": 8, "t": 28, "b": 12},
    )
    figure.add_trace(
        go.Heatmap(
            z=[_none_list(row) for row in table.to_numpy(dtype=float)],
            x=list(MONTHS),
            zmin=-month_bound,
            zmax=month_bound,
            name="Mes",
            hovertemplate="%{x} %{y}: %{z:.2%}<extra></extra>",
            **common,
        )
    )
    figure.add_trace(
        go.Heatmap(
            z=[[value] for value in _none_list(yearly.reindex(table.index))],
            x=["Año"],
            zmin=-year_bound,
            zmax=year_bound,
            name="Año",
            hovertemplate="Año %{y}: %{z:.2%}<extra></extra>",
            **common,
        )
    )
    figure.update_xaxes(
        type="category",
        side="top",
        showgrid=False,
        categoryorder="array",
        categoryarray=[*MONTHS, "Año"],
    )
    figure.update_yaxes(type="category", autorange="reversed", showgrid=False)
    return figure_payload(figure)


def _rolling_sharpe(returns: pd.Series, period: int) -> pd.Series:
    rolling = returns.rolling(period)
    sharpe = rolling.mean() / rolling.std() * sqrt(TRADING_DAYS)
    return sharpe.replace([np.inf, -np.inf], np.nan).dropna()


def rolling_sharpe_chart(
    portfolio: pd.Series, benchmark: pd.Series, period: int = ROLLING_SHARPE_PERIOD
) -> Markup | None:
    """Rolling annualized Sharpe (rf = 0) for portfolio and benchmark, with the mean."""
    returns = _clean(portfolio)
    if len(returns) < period:
        return None
    portfolio_sharpe = _rolling_sharpe(returns, period)
    if portfolio_sharpe.empty:
        return None
    figure = _figure(hovermode="x unified", showlegend=True, margin={"t": 32})
    name = _label(portfolio, "Portafolio")
    figure.add_trace(
        _line(portfolio_sharpe.index, portfolio_sharpe.to_numpy(), name, theme.PRIMARY, 2, "solid", ".2f")
    )
    bench = _clean(benchmark)
    if len(bench) >= period:
        bench_sharpe = _rolling_sharpe(bench, period)
        if not bench_sharpe.empty:
            bench_name = _label(benchmark, "Benchmark")
            figure.add_trace(
                _line(bench_sharpe.index, bench_sharpe.to_numpy(), bench_name, theme.TEXT_3, 1.5, "dot", ".2f")
            )
    mean = float(portfolio_sharpe.mean())
    figure.add_trace(
        go.Scatter(
            x=_iso_dates([portfolio_sharpe.index[0], portfolio_sharpe.index[-1]]),
            y=[mean, mean],
            name=f"Media {name} ({mean:.2f})".replace("-", "−"),
            mode="lines",
            line={"color": theme.PRIMARY, "width": 1, "dash": "dash"},
            hoverinfo="skip",
        )
    )
    _time_series_axes(figure, ".1f")
    figure.update_yaxes(hoverformat=".2f")
    return figure_payload(figure)


def returns_distribution_chart(portfolio: pd.Series, benchmark: pd.Series) -> Markup | None:
    """Overlaid histograms of monthly returns on shared bins."""
    monthly = _compound_monthly(portfolio)
    if monthly.empty:
        return None
    series = [(monthly, _label(portfolio, "Portafolio"), theme.PRIMARY, 0.7)]
    bench_monthly = _compound_monthly(benchmark)
    if not bench_monthly.empty:
        series.append((bench_monthly, _label(benchmark, "Benchmark"), theme.TEXT_DISABLED, 0.6))
    combined = np.concatenate([values.to_numpy() for values, *_ in series])
    edges = np.histogram_bin_edges(combined, bins="auto")
    if len(edges) < 2 or edges[-1] == edges[0]:
        size = 0.01
        start, end = float(combined.min()) - size / 2, float(combined.max()) + size / 2
    else:
        size = float(edges[1] - edges[0])
        start, end = float(edges[0]), float(edges[-1]) + size * 1e-6
    figure = _figure(
        barmode="overlay", hovermode="x unified", showlegend=True, margin={"t": 32}, bargap=0.04
    )
    for values, name, color, opacity in series:
        figure.add_trace(
            go.Histogram(
                x=values.to_numpy(),
                name=name,
                xbins={"start": start, "end": end, "size": size},
                marker={"color": color, "line": {"color": theme.SURFACE_1, "width": 1}},
                opacity=opacity,
                hovertemplate=f"{name}: %{{y}} meses<extra></extra>",
            )
        )
    figure.update_xaxes(
        tickformat=".0%", hoverformat=".1%", zeroline=True, showgrid=False, title={"text": "Retorno mensual"}
    )
    figure.update_yaxes(showgrid=True, title={"text": "Meses"}, rangemode="tozero")
    return figure_payload(figure)


def _yearly(returns: pd.Series | None) -> tuple[pd.Series, list[str]]:
    returns = _clean(returns)
    if returns.empty or not isinstance(returns.index, pd.DatetimeIndex):
        return pd.Series(dtype=float), []
    groups = returns.groupby(returns.index.year)
    yearly = groups.apply(lambda values: (1 + values).prod() - 1)
    spans = [f"{_date(values.index[0])} – {_date(values.index[-1])}" for _, values in groups]
    return yearly, spans


def yearly_returns_chart(portfolio: pd.Series, benchmark: pd.Series) -> Markup | None:
    """Grouped bars of calendar-year returns (portfolio vs benchmark)."""
    yearly, spans = _yearly(portfolio)
    if yearly.empty:
        return None
    figure = _figure(barmode="group", hovermode="x unified", showlegend=True, margin={"t": 32})
    show_text = len(yearly) <= 8
    series = [(yearly, spans, _label(portfolio, "Portafolio"), theme.PRIMARY)]
    bench_yearly, bench_spans = _yearly(benchmark)
    if not bench_yearly.empty:
        series.append((bench_yearly, bench_spans, _label(benchmark, "Benchmark"), theme.TEXT_DISABLED))
    for values, periods, name, color in series:
        figure.add_trace(
            go.Bar(
                x=[str(year) for year in values.index],
                y=values.to_numpy(),
                customdata=periods,
                name=name,
                marker={"color": color},
                texttemplate="%{y:.1%}" if show_text else None,
                textposition="outside",
                textfont={"size": 11, "color": theme.TEXT_1},
                cliponaxis=False,
                hovertemplate=f"{name}: %{{y:.2%}} (%{{customdata}})<extra></extra>",
            )
        )
    figure.update_xaxes(type="category", showgrid=False)
    figure.update_yaxes(tickformat=".0%", showgrid=True, zeroline=True)
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


# --------------------------------------------------------------------------- structure


def correlation_heatmap(matrix: pd.DataFrame, title: str = "Correlación") -> Markup | None:
    """Correlation matrix on the diverging scale (−1 blue · 0 gray · +1 red).

    Keeps the received order (e.g. HRP quasi-diagonal); cell values are written
    when there are 15 assets or fewer. ``title`` names the measure in the hover.
    """
    if matrix is None or matrix.empty:
        return None
    labels = [str(label) for label in matrix.columns]
    rows = [str(label) for label in matrix.index]
    size = len(labels)
    figure = _figure(
        height=int(min(640, max(320, 26 * size + 120))),
        showlegend=False,
        margin={"l": 64, "r": 8, "t": 12, "b": 56},
    )
    figure.add_trace(
        go.Heatmap(
            z=[_none_list(row) for row in matrix.to_numpy(dtype=float)],
            x=labels,
            y=rows,
            zmin=-1,
            zmid=0,
            zmax=1,
            colorscale=theme.CORRELATION_SCALE,
            xgap=2 if size <= 20 else 1,
            ygap=2 if size <= 20 else 1,
            hoverongaps=False,
            texttemplate="%{z:.2f}" if size <= 15 else None,
            textfont={"size": 11 if size <= 10 else 10},
            hovertemplate=f"<b>%{{y}} · %{{x}}</b><br>{title}: %{{z:.3f}}<extra></extra>",
            colorbar={
                "thickness": 10,
                "outlinewidth": 0,
                "len": 0.9,
                "tickvals": [-1, -0.5, 0, 0.5, 1],
                "tickformat": ".1f",
                "tickfont": {"size": 11, "color": theme.TEXT_3},
                "title": {"text": "ρ", "font": {"size": 12, "color": theme.TEXT_2}},
            },
        )
    )
    figure.update_xaxes(**_ticker_axis(showgrid=False, tickangle=-45 if size > 8 else 0))
    figure.update_yaxes(**_ticker_axis(showgrid=False, autorange="reversed"))
    return figure_payload(figure)


_SKFOLIO_ABOVE_THRESHOLD = "rgb(0,116,217)"


def dendrogram_chart(clustering) -> Markup | None:
    """Dendrogram of a fitted skfolio HierarchicalClustering estimator.

    Re-themes ``plot_dendrogram(heatmap=False)``: clusters take palette slots in
    order of appearance, links above the cut (single-asset clusters) are gray,
    and a dotted line marks the cut distance. skfolio applies optimal leaf
    ordering when drawing, so the leaf order can differ from the HRP order.
    """
    if clustering is None:
        return None
    try:
        source = clustering.plot_dendrogram(heatmap=False)
    except Exception:  # not fitted or degenerate input: no chart instead of a 500
        return None
    if not source.data:
        return None
    figure = _figure(hovermode="closest", showlegend=False, margin={"l": 56, "r": 16, "t": 28, "b": 56})
    cluster_colors: dict[str, str] = {}
    # One trace per color (links joined with None gaps) keeps the payload small.
    links: dict[str, tuple[list, list]] = {}
    for trace in source.data:
        original = str(getattr(trace.marker, "color", "") or "").replace(" ", "")
        if original == _SKFOLIO_ABOVE_THRESHOLD:
            color = theme.TEXT_3
        else:
            if original not in cluster_colors:
                position = len(cluster_colors)
                cluster_colors[original] = (
                    theme.SERIES[position] if position < theme.MAX_CATEGORICAL else theme.TEXT_DISABLED
                )
            color = cluster_colors[original]
        xs, ys = links.setdefault(color, ([], []))
        xs.extend([round(float(value), 4) for value in trace.x] + [None])
        ys.extend([round(float(value), 5) for value in trace.y] + [None])
    for color, (xs, ys) in links.items():
        figure.add_trace(
            go.Scatter(
                x=xs[:-1],
                y=ys[:-1],
                mode="lines",
                line={"color": color, "width": 1.75},
                hovertemplate="Distancia: %{y:.3f}<extra></extra>",
                showlegend=False,
            )
        )
    source_x = source.layout.xaxis
    tickvals = [float(value) for value in (source_x.tickvals if source_x.tickvals is not None else [])]
    ticktext = [str(text) for text in (source_x.ticktext if source_x.ticktext is not None else [])]
    if tickvals:
        figure.update_xaxes(range=[0, max(tickvals) + min(tickvals)])
    figure.update_xaxes(
        tickmode="array",
        tickvals=tickvals,
        ticktext=ticktext,
        showgrid=False,
        zeroline=False,
        tickfont={"family": MONO_FONT, "size": 11},
        tickangle=-45 if len(ticktext) > 10 else 0,
    )
    figure.update_yaxes(showgrid=True, rangemode="tozero", title={"text": "Distancia"}, hoverformat=".3f")
    try:
        n_clusters = int(clustering.n_clusters_)
        linkage = np.asarray(clustering.linkage_matrix_)
        if 1 < n_clusters <= len(linkage) + 1:
            cut = float(linkage[-(n_clusters - 1), 2])
            figure.add_shape(
                type="line",
                xref="paper",
                x0=0,
                x1=1,
                y0=cut,
                y1=cut,
                line={"color": theme.TEXT_3, "width": 1, "dash": "dot"},
                showlegend=True,
                name=f"Corte: {n_clusters} clusters",
            )
            figure.update_layout(showlegend=True, margin={"t": 36})
    except (AttributeError, TypeError, ValueError, IndexError):
        pass
    return figure_payload(figure)


def contribution_chart(
    frame: pd.DataFrame, column: str, *, compare: str | None = None
) -> Markup | None:
    """Horizontal bars of a per-asset contribution column (fractions), sorted.

    ``compare`` (optional): another column of ``frame`` (e.g. the weight) drawn
    as a tick marker per asset, to read risk contribution against weight.
    """
    if frame is None or column not in frame:
        return None
    values = _clean(frame[column])
    if values.empty:
        return None
    ordered = values.sort_values(ascending=False)
    tickers = [str(ticker) for ticker in ordered.index]
    data = ordered.to_numpy(dtype=float)
    has_compare = compare is not None and compare in frame
    figure = _figure(
        height=_bar_height(len(tickers), 28 if has_compare else 0),
        hovermode="y unified" if has_compare else "closest",
        showlegend=has_compare,
        margin={"l": 64, "r": 16, "t": 36 if has_compare else 12, "b": 36},
    )
    figure.add_trace(
        _value_bar(
            tickers,
            data,
            name=column,
            colors=[theme.NEGATIVE if value < 0 else theme.PRIMARY for value in data],
            width=0.66,
            hovertemplate=(
                f"{column}: %{{x:.2%}}<extra></extra>"
                if has_compare
                else f"<b>%{{y}}</b><br>{column}: %{{x:.2%}}<extra></extra>"
            ),
        )
    )
    range_values = list(data)
    if has_compare:
        other = _reindexed(frame[compare], tickers).dropna()
        if not other.empty:
            figure.add_trace(_tick_marker(list(other.index), other.to_numpy(dtype=float), compare))
            range_values += list(other.to_numpy(dtype=float))
    figure.update_xaxes(
        tickformat=".0%", hoverformat=".2%", showgrid=True, zeroline=True, range=_padded_range(range_values)
    )
    figure.update_yaxes(**_ticker_axis(autorange="reversed"))
    return figure_payload(figure)


# --------------------------------------------------------------------------- volatility


def _selector_menu(buttons: list[dict], active: int) -> list[dict]:
    """In-figure dropdown; buttons' ``label`` is what an external select matches."""
    if len(buttons) < 2:
        return []
    return [
        {
            "type": "dropdown",
            "direction": "down",
            "buttons": buttons,
            "active": active,
            "showactive": True,
            "x": 0,
            "xanchor": "left",
            "y": 1.02,
            "yanchor": "bottom",
            "pad": {"t": 0, "b": 4},
        }
    ]


def _histogram_trace(values: pd.Series, name: str, visible: bool, axis: str = "") -> go.Histogram:
    return go.Histogram(
        x=values.to_numpy(),
        name=name,
        visible=visible,
        xaxis=f"x{axis}",
        yaxis=f"y{axis}",
        marker={"color": theme.PRIMARY, "line": {"color": theme.SURFACE_1, "width": 1}},
        hovertemplate=f"{name} · volatilidad %{{x}}<br>Frecuencia: %{{y}}<extra></extra>",
        showlegend=False,
    )


def volatility_histogram_chart(
    monthly_volatility: pd.DataFrame,
    current: pd.Series | None = None,
    *,
    selected: str | None = None,
    axis_title: str = "Volatilidad mensual",
) -> Markup | None:
    """Monthly-volatility histogram per asset, one visible at a time (selector).

    ``current`` maps ticker → current volatility (dashed "Actual" guide); when
    omitted the last monthly value is used. ``selected`` picks the asset shown
    first; ``axis_title`` labels the x axis (e.g. "Volatilidad anualizada" when
    annualized values are passed). The in-figure dropdown (``updatemenus``) has one button per ticker
    whose ``label`` is the ticker, so an external ``<select>`` can drive it.
    """
    if monthly_volatility is None:
        return None
    columns = {str(ticker): _clean(monthly_volatility[ticker]) for ticker in monthly_volatility.columns}
    columns = {ticker: values for ticker, values in columns.items() if not values.empty}
    if not columns:
        return None
    tickers = list(columns)
    active = tickers.index(selected) if selected in tickers else 0
    current_values = pd.Series(current) if current is not None else None
    if current_values is not None:
        current_values.index = [str(ticker) for ticker in current_values.index]
    figure = _figure(showlegend=False, bargap=0.06, margin={"t": 44})
    markers = []
    for position, ticker in enumerate(tickers):
        figure.add_trace(_histogram_trace(columns[ticker], ticker, position == active))
        if current_values is None:
            value = float(columns[ticker].iloc[-1])
        else:
            value = _finite_or_none(current_values.get(ticker))
        if value is None:
            markers.append(([], []))
        else:
            shape, annotation = _vertical_marker(value, f"Actual {_percent(value)}")
            markers.append(([shape], [annotation]))
    buttons = [
        {
            "label": ticker,
            "method": "update",
            "args": [
                {"visible": [index == position for index in range(len(tickers))]},
                {"shapes": markers[position][0], "annotations": markers[position][1]},
            ],
        }
        for position, ticker in enumerate(tickers)
    ]
    figure.update_layout(
        shapes=markers[active][0],
        annotations=markers[active][1],
        updatemenus=_selector_menu(buttons, active),
    )
    figure.update_xaxes(tickformat=".1%", hoverformat=".2%", showgrid=False, title={"text": axis_title})
    figure.update_yaxes(showgrid=True, title={"text": "Meses"}, rangemode="tozero")
    return figure_payload(figure)


def returns_box_chart(returns: pd.DataFrame) -> Markup | None:
    """Box plot of daily returns per asset (outliers only, mean dashed).

    Colors follow the received ticker order; with more than 8 assets every box
    uses slot 1 (identity comes from the axis labels).
    """
    if returns is None:
        return None
    columns = {str(ticker): _clean(returns[ticker]) for ticker in returns.columns}
    columns = {ticker: values for ticker, values in columns.items() if not values.empty}
    if not columns:
        return None
    count = len(columns)
    figure = _figure(showlegend=False, hovermode="closest", margin={"t": 16})
    for position, (ticker, values) in enumerate(columns.items()):
        color = theme.series_color(position, count)
        figure.add_trace(
            go.Box(
                y=values.to_numpy(),
                name=ticker,
                boxpoints="outliers",
                boxmean=True,
                marker={"color": color, "size": 3, "opacity": 0.6},
                line={"color": color, "width": 1.5},
                fillcolor=theme.with_alpha(color, 0.16),
                whiskerwidth=0.4,
            )
        )
    figure.update_xaxes(**_ticker_axis(showgrid=False, tickangle=-45 if count > 12 else 0))
    figure.update_yaxes(
        tickformat=".1%", hoverformat=".2%", showgrid=True, zeroline=True, title={"text": "Retorno diario"}
    )
    return figure_payload(figure)


def prior_posterior_chart(prior: pd.Series, views: pd.Series, posterior: pd.Series) -> Markup | None:
    """Dumbbell per asset: prior (equilibrium) → posterior, with the user's view.

    Assets sorted by posterior return (largest on top); a gray connector shows
    the shift from prior to posterior.
    """
    posterior = _clean(posterior)
    if posterior.empty:
        return None
    posterior.index = [str(ticker) for ticker in posterior.index]
    ordered = posterior.sort_values(ascending=False)
    tickers = list(ordered.index)
    prior_values = _reindexed(prior, tickers)
    view_values = _reindexed(views, tickers)
    figure = _figure(
        height=max(200, 34 * len(tickers) + 100),
        hovermode="y unified",
        showlegend=True,
        margin={"l": 64, "r": 16, "t": 36, "b": 44},
    )
    connector_x: list = []
    connector_y: list = []
    for ticker in tickers:
        if pd.notna(prior_values[ticker]):
            connector_x += [float(prior_values[ticker]), float(ordered[ticker]), None]
            connector_y += [ticker, ticker, None]
    if connector_x:
        figure.add_trace(
            go.Scatter(
                x=connector_x,
                y=connector_y,
                mode="lines",
                name="Desplazamiento",
                line={"color": theme.BORDER_STRONG, "width": 3},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    traces = (
        (prior_values, "Prior (equilibrio)",
         {"symbol": "circle-open", "size": 11, "color": theme.TEXT_3, "line": {"width": 2, "color": theme.TEXT_3}}),
        (view_values, "View",
         {"symbol": "diamond", "size": 11, "color": theme.SERIES[1], "line": {"width": 1.5, "color": theme.SURFACE_1}}),
        (ordered, "Posterior",
         {"symbol": "circle", "size": 12, "color": theme.PRIMARY, "line": {"width": 2, "color": theme.SURFACE_1}}),
    )
    range_values = list(ordered.to_numpy(dtype=float))
    for values, name, marker in traces:
        present = values.dropna()
        if present.empty:
            continue
        figure.add_trace(
            go.Scatter(
                x=present.to_numpy(dtype=float),
                y=list(present.index),
                mode="markers",
                name=name,
                marker=marker,
                hovertemplate=f"{name}: %{{x:.2%}}<extra></extra>",
            )
        )
        range_values += list(present.to_numpy(dtype=float))
    figure.update_xaxes(
        tickformat=".1%",
        hoverformat=".2%",
        showgrid=True,
        zeroline=True,
        range=_padded_range(range_values, pad=0.08),
        title={"text": "Retorno anual esperado"},
    )
    figure.update_yaxes(**_ticker_axis(autorange="reversed", showgrid=False))
    return figure_payload(figure)


def period_volatility_chart(
    annualized: pd.DataFrame,
    period_volatility: pd.DataFrame,
    label: str,
    *,
    selected: str | None = None,
) -> Markup | None:
    """Latest annualized volatility per asset (bars) for the options view.

    The dropdown switches between the summary bars ("Todos los activos") and the
    annualized-volatility histogram of one asset with its "Actual" guide.
    ``label`` names the period (e.g. "Mensual"). ``selected`` (optional) opens
    the histogram of that ticker first.
    """
    if annualized is None or annualized.dropna(how="all").empty:
        return None
    if period_volatility is None:
        period_volatility = pd.DataFrame()
    histories: dict[str, pd.Series] = {}
    latest: dict[str, tuple[float, float | None, str]] = {}
    for ticker in annualized.columns:
        values = _clean(annualized[ticker])
        if values.empty:
            continue
        name = str(ticker)
        histories[name] = values
        last_date = values.index[-1]
        period_value = None
        if ticker in period_volatility.columns and last_date in period_volatility.index:
            period_value = _finite_or_none(period_volatility.at[last_date, ticker])
        latest[name] = (float(values.iloc[-1]), period_value, _date(last_date))
    if not latest:
        return None

    ranking = sorted(latest, key=lambda ticker: latest[ticker][0], reverse=True)
    bars_height = _bar_height(len(ranking), 28)
    hist_height = 380
    figure = _figure(showlegend=False, bargap=0.06, margin={"l": 64, "r": 16, "t": 44, "b": 44})
    figure.add_trace(
        _value_bar(
            ranking,
            [latest[ticker][0] for ticker in ranking],
            name="Volatilidad anualizada actual",
            colors=theme.PRIMARY,
            width=0.66,
            customdata=[[latest[ticker][1], latest[ticker][2]] for ticker in ranking],
            hovertemplate=(
                "<b>%{y}</b> · periodo al %{customdata[1]}<br>Anualizada: %{x:.2%}"
                f"<br>Volatilidad del periodo ({label}): %{{customdata[0]:.2%}}<extra></extra>"
            ),
        )
    )
    tickers = list(histories)
    for ticker in tickers:
        figure.add_trace(_histogram_trace(histories[ticker], ticker, False, axis="2"))

    trace_count = 1 + len(tickers)
    buttons = [
        {
            "label": "Todos los activos",
            "method": "update",
            "args": [
                {"visible": [index == 0 for index in range(trace_count)]},
                {
                    "xaxis.visible": True,
                    "yaxis.visible": True,
                    "xaxis2.visible": False,
                    "yaxis2.visible": False,
                    "shapes": [],
                    "annotations": [],
                    "height": bars_height,
                },
            ],
        }
    ]
    for position, ticker in enumerate(tickers, start=1):
        value, period_value, _ = latest[ticker]
        text = f"Actual {_percent(value)}"
        if period_value is not None:
            text += f" · periodo {_percent(period_value)}"
        shape, annotation = _vertical_marker(value, text, xref="x2", yref="y2 domain")
        buttons.append(
            {
                "label": ticker,
                "method": "update",
                "args": [
                    {"visible": [index == position for index in range(trace_count)]},
                    {
                        "xaxis.visible": False,
                        "yaxis.visible": False,
                        "xaxis2.visible": True,
                        "yaxis2.visible": True,
                        "shapes": [shape],
                        "annotations": [annotation],
                        "height": hist_height,
                    },
                ],
            }
        )
    figure.update_layout(
        height=bars_height,
        updatemenus=_selector_menu(buttons, 0),
        xaxis={
            "tickformat": ".0%",
            "hoverformat": ".2%",
            "showgrid": True,
            "zeroline": True,
            "range": _padded_range([latest[ticker][0] for ticker in ranking]),
        },
        yaxis=_ticker_axis(autorange="reversed", showgrid=False),
        xaxis2={
            "anchor": "y2",
            "domain": [0, 1],
            "visible": False,
            "tickformat": ".0%",
            "hoverformat": ".2%",
            "showgrid": False,
            "title": {"text": f"Volatilidad anualizada · {label}"},
        },
        yaxis2={
            "anchor": "x2",
            "domain": [0, 1],
            "visible": False,
            "showgrid": True,
            "rangemode": "tozero",
            "title": {"text": "Periodos"},
        },
    )
    if selected in tickers:
        active = tickers.index(selected) + 1
        trace_args, layout_args = buttons[active]["args"]
        for trace, visible in zip(figure.data, trace_args["visible"]):
            trace.visible = visible
        figure.update_layout(layout_args)
        figure.layout.updatemenus[0].active = active
    return figure_payload(figure)
