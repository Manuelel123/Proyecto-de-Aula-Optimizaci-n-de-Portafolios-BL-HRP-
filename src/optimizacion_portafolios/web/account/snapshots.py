"""Signed portfolio snapshots carried by the "Guardar portafolio" form.

A result page embeds the snapshot (model, weights, metrics, parameters) as a
signed token, so saving needs no recalculation and the server only stores what
it computed itself. Tokens expire after a day.
"""

from __future__ import annotations

import math
from datetime import date

from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer

_SALT = "atlas-portfolio-snapshot"
MAX_AGE_SECONDS = 24 * 60 * 60
MODEL_LABELS = {"hrp": "HRP", "black_litterman": "Black-Litterman"}


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt=_SALT)


def _clean(value):
    """Plain JSON value: numpy scalars become floats and NaN becomes ``None``."""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def sign(snapshot: dict) -> str:
    return _serializer().dumps(_clean(snapshot))


def unsign(token: str) -> dict | None:
    try:
        snapshot = _serializer().loads(token, max_age=MAX_AGE_SECONDS)
    except BadSignature:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("model") not in MODEL_LABELS:
        return None
    if not isinstance(snapshot.get("weights"), dict) or not snapshot["weights"]:
        return None
    return snapshot


def default_name(snapshot: dict) -> str:
    count = len(snapshot.get("weights") or {})
    return f"{MODEL_LABELS[snapshot['model']]} · {count} activos · {date.today().isoformat()}"


def hrp_snapshot(result: dict) -> dict:
    summary = result["summary"]
    return {
        "model": "hrp",
        "benchmark": result["benchmark"],
        "start_date": result["start"],
        "end_date": result["end"],
        "weights": summary["weights"],
        "metrics": summary["metrics"],
        "params": {"bounds": summary["bounds"]},
    }


def black_litterman_snapshot(
    result: dict, objective: str, benchmark: str, risk_free_rate: float
) -> dict:
    rows = result["allocation_rows"]
    history = result.get("history") or {}
    return {
        "model": "black_litterman",
        "benchmark": benchmark,
        "start_date": result["window"]["effective_start_iso"],
        "end_date": result["window"]["effective_end_iso"],
        "weights": {row["ticker"]: row["weight"] for row in rows},
        "metrics": {
            "expected_return": result["expected_return"],
            "volatility": result["volatility"],
            "sharpe": result["sharpe"],
            "sortino": result["sortino"],
            "max_drawdown": history.get("max_drawdown"),
        },
        "params": {
            "objective": objective,
            "risk_free_rate": risk_free_rate,
            "views": {row["ticker"]: row["view"] for row in rows},
            "market_weights": {row["ticker"]: row["market_weight"] for row in rows},
        },
    }
