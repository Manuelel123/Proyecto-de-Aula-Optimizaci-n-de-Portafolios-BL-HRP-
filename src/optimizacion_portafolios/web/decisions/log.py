"""Glue between the result pages and the decision log (``storage.decisions``).

``record`` is called right after HRP or Black-Litterman produced a portfolio
(and when one is fixed). It stamps the signed-in user, the current profile
(``session["perfil"]``) and the role. A database failure never breaks the
result page: it is logged and the user sees a warning.
"""

from __future__ import annotations

import logging

from flask import flash
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from optimizacion_portafolios.storage.decisions import record_decision
from optimizacion_portafolios.web.profile.access import current_profile

logger = logging.getLogger(__name__)


def record(method: str, weights: dict[str, float], params: dict, source: str | None = None) -> bool:
    """Insert one decision row for the signed-in user; ``True`` if stored."""
    if not current_user.is_authenticated or not weights:
        return False
    profile = current_profile() or {}
    # The gestor has no risk profile ("modo original"): the row says so.
    profile_level = "gestor" if current_user.role == "gestor" else profile.get("perfil_final")
    try:
        record_decision(
            current_user.id,
            method=method,
            source=source or method,
            weights=weights,
            params=params,
            profile_id=profile.get("id"),
            profile_level=profile_level,
            role=current_user.role,
        )
    except (SQLAlchemyError, ValueError):
        logger.exception("Recording a decision failed")
        flash("No se pudo guardar este resultado en tu historial de decisiones.", "warning")
        return False
    return True


def hrp_weights(result: dict) -> dict[str, float]:
    return dict(result["summary"]["weights"])


def black_litterman_weights(result: dict) -> dict[str, float]:
    return {row["ticker"]: row["weight"] for row in result["allocation_rows"]}
