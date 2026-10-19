"""Signed-in user pages: HRP backtest history (InsForge)."""

import logging
import re

from flask import abort, flash, redirect, render_template, url_for

from optimizacion_portafolios.insforge import InsForgeError
from optimizacion_portafolios.web.account import bp, repository
from optimizacion_portafolios.web.auth.session import (
    access_token,
    insforge_client,
    login_required,
)

logger = logging.getLogger(__name__)

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _checked_id(value: str) -> str:
    if not _UUID.match(value):
        abort(404)
    return value


def _context():
    return insforge_client(), access_token()


@bp.get("/backtests")
@login_required
def backtests():
    try:
        runs = repository.list_backtests(*_context())
    except InsForgeError as error:
        flash(error.user_message, "error")
        runs = []
    return render_template("account/backtests.html", runs=runs)


@bp.get("/backtests/<run_id>")
@login_required
def backtest_detail(run_id: str):
    try:
        run = repository.get_backtest(*_context(), _checked_id(run_id))
    except InsForgeError as error:
        flash(error.user_message, "error")
        return redirect(url_for("account.backtests"))
    if run is None:
        abort(404)
    return render_template("account/backtest_detail.html", run=run)


@bp.post("/backtests/<run_id>/eliminar")
@login_required
def delete_backtest(run_id: str):
    try:
        if repository.delete_backtest(*_context(), _checked_id(run_id)):
            flash("Backtest eliminado del historial.", "info")
    except InsForgeError as error:
        flash(error.user_message, "error")
    return redirect(url_for("account.backtests"))
