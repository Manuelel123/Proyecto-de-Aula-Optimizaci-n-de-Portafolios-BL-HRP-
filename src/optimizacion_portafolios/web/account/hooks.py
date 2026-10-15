"""Glue between the model pages and the user's account (save, history)."""

import logging

from optimizacion_portafolios.insforge import InsForgeError
from optimizacion_portafolios.web.account import repository
from optimizacion_portafolios.web.account.snapshots import default_name, sign
from optimizacion_portafolios.web.auth.session import (
    access_token,
    current_user,
    insforge_client,
)

logger = logging.getLogger(__name__)


def save_form(snapshot: dict) -> dict:
    """Template data of the "Guardar portafolio" control of a result page."""
    return {"token": sign(snapshot), "default_name": default_name(snapshot)}


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
