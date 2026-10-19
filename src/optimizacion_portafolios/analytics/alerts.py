"""Alert rules of a tracked portfolio: definitions and evaluation (pure).

Each rule reads one value from the tracking metrics and compares it with a
yellow and a red threshold (the value must be strictly above a threshold to
reach its level). Thresholds are stored in natural units: fractions for
percentages (0.10 = 10 %), a multiple for the volatility ratio and days for
the negative-Sharpe streak and stale prices.
"""

from __future__ import annotations

from dataclasses import dataclass

GREEN, YELLOW, RED = "green", "yellow", "red"
LEVEL_ORDER = {GREEN: 0, YELLOW: 1, RED: 2}
LEVEL_LABELS = {GREEN: "Verde", YELLOW: "Amarillo", RED: "Rojo"}


@dataclass(frozen=True)
class RuleSpec:
    kind: str
    label: str
    description: str
    unit: str  # "pct" (fraction shown in %), "pp" (points), "x" (multiple), "days", "ratio"
    yellow: float | None
    red: float | None


RULES: tuple[RuleSpec, ...] = (
    RuleSpec("drawdown", "Drawdown", "Caída actual desde el máximo previo.", "pct", 0.10, 0.15),
    RuleSpec(
        "volatility",
        "Volatilidad móvil",
        "Volatilidad de 21 días frente a su promedio desde la fijación.",
        "x",
        1.5,
        2.0,
    ),
    RuleSpec("weight_drift", "Desviación de pesos", "Mayor desviación de un peso frente a su objetivo.", "pp", 0.05, 0.10),
    RuleSpec(
        "negative_sharpe",
        "Sharpe móvil negativo",
        "Días seguidos con el Sharpe móvil de 126 días por debajo de cero.",
        "days",
        63,
        126,
    ),
    RuleSpec("risk_concentration", "Concentración del riesgo", "Mayor participación de un activo en el riesgo total.", "pct", 0.30, 0.40),
    RuleSpec("correlation", "Correlación promedio", "Correlación promedio móvil de 63 días entre los activos.", "ratio", 0.70, 0.85),
    RuleSpec(
        "stale_price",
        "Activo sin cotización",
        "Días hábiles sin precio nuevo de un activo (puede haber dejado de cotizar).",
        "days",
        2,
        5,
    ),
)
RULES_BY_KIND = {rule.kind: rule for rule in RULES}


@dataclass(frozen=True)
class Signal:
    kind: str
    level: str
    value: float | None
    message: str


def _value(kind: str, scalars: dict) -> float | None:
    if kind == "drawdown":
        drawdown = scalars.get("drawdown")
        return abs(drawdown) if drawdown is not None else None
    if kind == "volatility":
        current, average = scalars.get("rolling_volatility_21"), scalars.get("average_rolling_volatility_21")
        return current / average if current is not None and average else None
    if kind == "weight_drift":
        return scalars.get("max_abs_drift")
    if kind == "negative_sharpe":
        return scalars.get("negative_sharpe_days") if scalars.get("rolling_sharpe_126") is not None else None
    if kind == "risk_concentration":
        return scalars.get("max_risk_share")
    if kind == "correlation":
        return scalars.get("average_correlation")
    if kind == "stale_price":
        return scalars.get("max_stale_days")
    raise ValueError(f"Unknown alert rule: {kind}")


def format_value(unit: str, value: float | None) -> str:
    if value is None:
        return "N/D"
    if unit == "pct":
        return f"{value * 100:.1f} %"
    if unit == "pp":
        return f"{value * 100:.1f} pp"
    if unit == "x":
        return f"{value:.2f}×"
    if unit == "days":
        return f"{int(value)} días"
    return f"{value:.2f}"


def level_for(value: float | None, yellow: float | None, red: float | None) -> str:
    if value is None:
        return GREEN
    if red is not None and value > red:
        return RED
    if yellow is not None and value > yellow:
        return YELLOW
    return GREEN


def evaluate(scalars: dict, thresholds: dict[str, tuple[float | None, float | None]], context: dict | None = None) -> list[Signal]:
    """One signal per enabled rule (``thresholds``: kind -> (yellow, red)).

    ``context`` adds details to the messages (``drift_ticker``,
    ``risk_ticker``, ``stale_ticker``).
    """
    context = context or {}
    signals = []
    for kind, (yellow, red) in thresholds.items():
        spec = RULES_BY_KIND[kind]
        value = _value(kind, scalars)
        level = level_for(value, yellow, red)
        detail = {
            "weight_drift": context.get("drift_ticker"),
            "risk_concentration": context.get("risk_ticker"),
            "stale_price": context.get("stale_ticker"),
        }.get(kind)
        message = f"{spec.label}: {format_value(spec.unit, value)}"
        if detail and level != GREEN:
            message += f" ({detail})"
        if level != GREEN:
            limit = red if level == RED else yellow
            message += f" · umbral {LEVEL_LABELS[level].lower()} {format_value(spec.unit, limit)}"
        signals.append(Signal(kind, level, value, message))
    return signals


def overall_level(levels) -> str:
    """Worst level of a collection (green when empty)."""
    return max(levels, key=LEVEL_ORDER.__getitem__, default=GREEN)
