"""Chart tokens and the light Plotly template "atlas".

The hex values replicate the light-theme tokens of docs/diseno_visual.md. Figures
are serialized with these light values; static/js/charts.js swaps every known
token hex (also as the ``#rrggbbaa`` form with alpha) for the value of its CSS
variable at render time, so the same figure follows the light/dark theme.
Keep ``CSS_VARIABLES`` and the map in charts.js in sync.
"""

import plotly.graph_objects as go

# Categorical series palette (fixed order, validated; never reorder or extend).
SERIES = (
    "#2a78d6",  # 1 blue
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 green
    "#4a3aa7",  # 7 violet
    "#e34948",  # 8 red
)
PRIMARY = SERIES[0]

TEXT_1 = "#0f172a"
TEXT_2 = "#475569"
TEXT_3 = "#5f6b7e"
TEXT_DISABLED = "#9aa3b2"
BORDER = "#e3e7ed"
BORDER_STRONG = "#cfd5de"
SURFACE_1 = "#ffffff"
SURFACE_3 = "#e8ebf0"
POSITIVE = "#16a34a"
NEGATIVE = "#dc2626"

# Diverging correlation scale (-1 blue .. 0 neutral gray .. +1 red).
DIVERGING = ("#104281", "#256abf", "#86b6ef", "#f0efec", "#f3a29f", "#d03b3b", "#8f1d1d")
CORRELATION_SCALE = [
    [0.0, DIVERGING[0]],
    [0.2, DIVERGING[1]],
    [0.35, DIVERGING[2]],
    [0.5, DIVERGING[3]],
    [0.65, DIVERGING[4]],
    [0.8, DIVERGING[5]],
    [1.0, DIVERGING[6]],
]
# Returns: losses red, gains blue (CVD-safe; red keeps the "loss" meaning).
RETURNS_SCALE = [[1 - stop, color] for stop, color in reversed(CORRELATION_SCALE)]

# Token hex -> CSS variable defined in static/css/app.css (mirrored in charts.js).
CSS_VARIABLES = {
    **{color: f"--series-{index}" for index, color in enumerate(SERIES, start=1)},
    TEXT_1: "--text-1",
    TEXT_2: "--text-2",
    TEXT_3: "--text-3",
    TEXT_DISABLED: "--text-disabled",
    BORDER: "--border",
    BORDER_STRONG: "--border-strong",
    SURFACE_1: "--surface-1",
    SURFACE_3: "--surface-3",
    POSITIVE: "--positive-chart",
    NEGATIVE: "--negative-chart",
    **dict(zip(DIVERGING, ("--corr-neg-3", "--corr-neg-2", "--corr-neg-1", "--corr-mid",
                           "--corr-pos-1", "--corr-pos-2", "--corr-pos-3"))),
}

FONT_FAMILY = "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
MAX_CATEGORICAL = len(SERIES)


def with_alpha(color: str, alpha: float) -> str:
    """Token hex with alpha as ``#rrggbbaa`` (charts.js keeps the alpha)."""
    return f"{color}{round(alpha * 255):02x}"


def series_color(position: int, count: int | None = None) -> str:
    """Color for the series at ``position`` in the received order.

    With more series than palette slots no new hue is generated: every series
    falls back to slot 1 and identity comes from labels instead of color.
    """
    if count is not None and count > MAX_CATEGORICAL:
        return PRIMARY
    if position >= MAX_CATEGORICAL:
        return TEXT_DISABLED
    return SERIES[position]


def _axis() -> dict:
    return {
        "showgrid": False,
        "gridcolor": BORDER,
        "gridwidth": 1,
        "zeroline": False,
        "zerolinecolor": BORDER_STRONG,
        "zerolinewidth": 1,
        "showline": False,
        "ticks": "",
        "tickfont": {"size": 11, "color": TEXT_3},
        "title": {"font": {"size": 12, "color": TEXT_2}, "standoff": 8},
        "automargin": True,
        "linecolor": BORDER_STRONG,
        "spikecolor": TEXT_3,
        "spikethickness": 1,
        "spikedash": "dot",
        "spikemode": "across",
    }


ATLAS_TEMPLATE = go.layout.Template(
    layout={
        "font": {"family": FONT_FAMILY, "size": 12, "color": TEXT_2},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "colorway": list(SERIES),
        "separators": ".,",
        "margin": {"l": 56, "r": 16, "t": 24, "b": 44, "pad": 0},
        "autosize": True,
        "hovermode": "closest",
        "hoverlabel": {
            "bgcolor": SURFACE_1,
            "bordercolor": BORDER_STRONG,
            "font": {"family": FONT_FAMILY, "size": 12, "color": TEXT_1},
            "align": "left",
            "namelength": -1,
        },
        "legend": {
            "orientation": "h",
            "x": 0,
            "xanchor": "left",
            "y": 1.02,
            "yanchor": "bottom",
            "font": {"size": 12, "color": TEXT_2},
            "bgcolor": "rgba(0,0,0,0)",
            "itemclick": "toggle",
            "itemdoubleclick": "toggleothers",
        },
        "xaxis": _axis(),
        "yaxis": _axis(),
        "bargap": 0.25,
        "bargroupgap": 0.08,
        "barcornerradius": 4,
        "colorscale": {"diverging": CORRELATION_SCALE},
        "updatemenudefaults": {
            "bgcolor": SURFACE_1,
            "bordercolor": BORDER_STRONG,
            "borderwidth": 1,
            "font": {"size": 12, "color": TEXT_1},
        },
        "annotationdefaults": {
            "font": {"size": 11, "color": TEXT_2},
            "showarrow": False,
        },
    }
)
