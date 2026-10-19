"""Alert rules of tracked portfolios: forms, evaluation and view models."""

from __future__ import annotations

from math import isfinite

import pandas as pd
from flask import request

from optimizacion_portafolios.analytics.alerts import (
    LEVEL_LABELS,
    RULES,
    RULES_BY_KIND,
    evaluate,
    format_value,
    overall_level,
)
from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.storage.models import Portfolio

# Form inputs are typed in percent for "pct"/"pp" rules and as-is otherwise.
_PERCENT_UNITS = {"pct", "pp"}
UNIT_SUFFIX = {"pct": "%", "pp": "pp", "x": "×", "days": "días", "ratio": ""}


def default_rules() -> dict[str, tuple[float | None, float | None, bool]]:
    return {rule.kind: (rule.yellow, rule.red, True) for rule in RULES}


def ensure_rules(portfolio: Portfolio) -> None:
    """Create the default rules of portfolios fixed before alerts existed."""
    missing = {kind: spec for kind, spec in default_rules().items() if kind not in {r.kind for r in portfolio.alert_rules}}
    if missing:
        repositories.set_alert_rules(portfolio, missing)


def _display(unit: str, value: float | None) -> str:
    if value is None:
        return ""
    shown = value * 100 if unit in _PERCENT_UNITS else value
    return f"{shown:g}"


def form_rows(rules: dict[str, tuple[float | None, float | None, bool]] | None = None) -> list[dict]:
    """Rows of the thresholds form (values in display units)."""
    rules = rules or default_rules()
    rows = []
    for spec in RULES:
        yellow, red, enabled = rules.get(spec.kind, (spec.yellow, spec.red, True))
        rows.append(
            {
                "kind": spec.kind,
                "label": spec.label,
                "description": spec.description,
                "suffix": UNIT_SUFFIX[spec.unit],
                "yellow": _display(spec.unit, yellow),
                "red": _display(spec.unit, red),
                "enabled": enabled,
                "default": f"{_display(spec.unit, spec.yellow)} / {_display(spec.unit, spec.red)} {UNIT_SUFFIX[spec.unit]}".strip(),
            }
        )
    return rows


def submitted_rows() -> list[dict]:
    """Form rows refilled with what the user typed (after a validation error)."""
    rows = form_rows()
    for row in rows:
        kind = row["kind"]
        row["yellow"] = request.form.get(f"alert_{kind}_yellow", "")
        row["red"] = request.form.get(f"alert_{kind}_red", "")
        row["enabled"] = bool(request.form.get(f"alert_{kind}_enabled"))
    return rows


def _threshold(field: str, label: str, unit: str) -> float | None:
    raw = request.form.get(field, "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"El umbral de «{label}» debe ser numérico.") from error
    if not isfinite(value) or value < 0:
        raise ValueError(f"El umbral de «{label}» debe ser un número positivo.")
    return value / 100 if unit in _PERCENT_UNITS else value


def parse_rules_form() -> dict[str, tuple[float | None, float | None, bool]]:
    """Thresholds typed in the form (``alert_<kind>_yellow|red|enabled``).

    A form without the thresholds table (no ``alerts_form`` field) keeps the
    default rules.
    """
    if not request.form.get("alerts_form"):
        return default_rules()
    rules = {}
    for spec in RULES:
        yellow = _threshold(f"alert_{spec.kind}_yellow", spec.label, spec.unit)
        red = _threshold(f"alert_{spec.kind}_red", spec.label, spec.unit)
        if yellow is not None and red is not None and red < yellow:
            raise ValueError(f"En «{spec.label}» el umbral rojo debe ser mayor o igual que el amarillo.")
        rules[spec.kind] = (yellow, red, bool(request.form.get(f"alert_{spec.kind}_enabled")))
    return rules


def current_rules(portfolio: Portfolio) -> dict[str, tuple[float | None, float | None, bool]]:
    return {rule.kind: (rule.yellow, rule.red, rule.enabled) for rule in portfolio.alert_rules}


def evaluate_portfolio(portfolio: Portfolio, analysis: dict):
    """Evaluate the enabled rules on the latest valued day and sync the events."""
    ensure_rules(portfolio)
    thresholds = {
        rule.kind: (rule.yellow, rule.red)
        for rule in portfolio.alert_rules
        if rule.enabled and rule.kind in RULES_BY_KIND
    }
    metrics = analysis["metrics"]
    signals = evaluate(metrics["scalars"], thresholds, metrics.get("context"))
    as_of = pd.Timestamp(analysis["total"].index[-1]).date()
    repositories.sync_alerts(portfolio, signals, as_of)
    return signals


def event_row(event) -> dict:
    spec = RULES_BY_KIND.get(event.kind)
    return {
        "label": spec.label if spec else event.kind,
        "level": event.level,
        "level_label": LEVEL_LABELS.get(event.level, event.level),
        "message": event.message,
        "triggered_on": event.triggered_on.isoformat(),
        "resolved_on": event.resolved_on.isoformat() if event.resolved_on else None,
        "updated_on": event.updated_on.isoformat(),
    }


def status_view(portfolio: Portfolio, signals=None) -> dict:
    """Traffic light, open alerts and, when evaluated now, every rule's value."""
    open_events = repositories.open_alerts(portfolio)
    level = overall_level(event.level for event in open_events)
    view = {
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "open": [event_row(event) for event in open_events],
        "rules": [],
    }
    for signal in signals or []:
        spec = RULES_BY_KIND[signal.kind]
        view["rules"].append(
            {
                "label": spec.label,
                "level": signal.level,
                "level_label": LEVEL_LABELS[signal.level],
                "value": format_value(spec.unit, signal.value),
            }
        )
    return view

