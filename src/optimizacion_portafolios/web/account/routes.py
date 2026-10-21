"""Signed-in user pages: HRP backtest history (local database)."""

import re

from flask import abort, flash, redirect, render_template, url_for

from optimizacion_portafolios.web.account import bp, repository
from optimizacion_portafolios.web.auth.session import current_user_id, login_required

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _checked_id(value: str) -> str:
    if not _UUID.match(value):
        abort(404)
    return value


@bp.get("/backtests")
@login_required
def backtests():
    return render_template("account/backtests.html", runs=repository.list_backtests(current_user_id()))


@bp.get("/backtests/<run_id>")
@login_required
def backtest_detail(run_id: str):
    run = repository.get_backtest(current_user_id(), _checked_id(run_id))
    if run is None:
        abort(404)
    return render_template("account/backtest_detail.html", run=run)


@bp.post("/backtests/<run_id>/eliminar")
@login_required
def delete_backtest(run_id: str):
    if repository.delete_backtest(current_user_id(), _checked_id(run_id)):
        flash("Backtest eliminado del historial.", "info")
    return redirect(url_for("account.backtests"))
