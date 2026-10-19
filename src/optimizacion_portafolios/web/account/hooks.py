"""Glue between the HRP page and the user's backtest history (InsForge)."""

import logging

from optimizacion_portafolios.insforge import InsForgeError
from optimizacion_portafolios.web.account import repository
from optimizacion_portafolios.web.auth.session import (
    access_token,
    current_user,
    insforge_client,
)

logger = logging.getLogger(__name__)


def record_backtest(backtest: dict | None) -> None:
    """Add a successful backtest to the signed-in user's history.

    Sets ``backtest["history"]`` to ``{"id", "created"}`` or ``{"error"}``; does
    nothing for anonymous users or when accounts are not configured.
    """
    if not backtest or backtest.get("error") or "record" not in backtest:
        return
    client = insforge_client()
    if client is None or current_user() is None:
        return
    try:
        token = access_token()
        if token is None:
            return
        run_id, created = repository.record_backtest(client, token, backtest["record"])
    except InsForgeError as error:
        logger.warning("Recording a backtest failed: %s", error)
        backtest["history"] = {"error": error.user_message}
        return
    backtest["history"] = {"id": run_id, "created": created}
