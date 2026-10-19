"""Decision log (``registro_decisiones``): insert and read, nothing else.

There is deliberately no update or delete function; the model also refuses
both at the ORM level, so the history stays verifiable.
"""

from __future__ import annotations

import math

from sqlalchemy import func, select

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import DECISION_METHODS, DECISION_SOURCES, DecisionRecord


def _plain(value):
    """JSON-safe copy: numpy scalars become floats, NaN/inf become ``None``."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def record_decision(
    user_id: int | str,
    *,
    method: str,
    source: str,
    weights: dict[str, float],
    params: dict | None = None,
    profile_id: int | None = None,
    profile_level: str | None = None,
    role: str,
) -> DecisionRecord:
    """Insert one row and commit."""
    if method not in DECISION_METHODS:
        raise ValueError(f"Método desconocido: {method}")
    if source not in DECISION_SOURCES:
        raise ValueError(f"Origen desconocido: {source}")
    if not weights:
        raise ValueError("Un registro de decisión necesita los pesos del portafolio.")
    record = DecisionRecord(
        usuario_id=int(user_id),
        perfil_id=profile_id,
        perfil_final=profile_level,
        rol=role,
        metodo=method,
        origen=source,
        activos=_plain(dict(weights)),
        parametros=_plain(params or {}),
    )
    try:
        db.session.add(record)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return record


def list_decisions(user_id: int | str, limit: int = 100) -> list[DecisionRecord]:
    """The user's records, newest first."""
    return list(
        db.session.scalars(
            select(DecisionRecord)
            .where(DecisionRecord.usuario_id == int(user_id))
            .order_by(DecisionRecord.fecha.desc(), DecisionRecord.id.desc())
            .limit(limit)
        )
    )


def count_decisions(user_id: int | str) -> int:
    return db.session.scalar(
        select(func.count(DecisionRecord.id)).where(DecisionRecord.usuario_id == int(user_id))
    )
