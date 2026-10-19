"""Glue between the HRP page and the user's backtest history (local database)."""

import logging

from sqlalchemy.exc import SQLAlchemyError

from optimizacion_portafolios.web.account import repository
from optimizacion_portafolios.web.auth.session import current_user_id

logger = logging.getLogger(__name__)


def record_backtest(backtest: dict | None) -> None:
    """Add a successful backtest to the signed-in user's history.

    Sets ``backtest["history"]`` to ``{"id", "created"}`` or ``{"error"}``; does
    nothing without a signed-in user.
    """
    if not backtest or backtest.get("error") or "record" not in backtest:
        return
    user_id = current_user_id()
    if user_id is None:
        return
    try:
        run_id, created = repository.record_backtest(user_id, backtest["record"])
    except SQLAlchemyError:
        logger.exception("Recording a backtest failed")
        backtest["history"] = {"error": "Error de la base de datos local. Intenta de nuevo."}
        return
    backtest["history"] = {"id": run_id, "created": created}
