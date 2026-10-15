"""Signed-in user pages: saved portfolios and backtest history."""

import logging
import re

from flask import abort, flash, redirect, render_template, request, url_for

from optimizacion_portafolios.insforge import InsForgeError
from optimizacion_portafolios.web.account import bp, repository
from optimizacion_portafolios.web.account.snapshots import MODEL_LABELS, default_name, unsign
from optimizacion_portafolios.web.auth.session import (
    access_token,
    insforge_client,
    login_required,
    safe_next,
)

logger = logging.getLogger(__name__)

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NAME_MAX = 120


def _checked_id(value: str) -> str:
    if not _UUID.match(value):
        abort(404)
    return value


def _name_field(fallback: str = "") -> str:
    name = " ".join(request.form.get("name", "").split())[:_NAME_MAX] or fallback
    if not name:
        raise ValueError("Escribe un nombre para el portafolio.")
    return name


def _context():
    return insforge_client(), access_token()


@bp.post("/portafolios/guardar")
@login_required
def save_portfolio():
    next_url = safe_next(request.form.get("next"), "account.portfolios")
    snapshot = unsign(request.form.get("snapshot", ""))
    if snapshot is None:
        flash("El resultado expiró o no es válido. Vuelve a calcular el portafolio.", "error")
        return redirect(next_url)
    try:
        name = _name_field(default_name(snapshot))
        client, token = _context()
        portfolio_id = repository.save_portfolio(client, token, snapshot, name)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(next_url)
    except InsForgeError as error:
        logger.warning("Saving a portfolio failed: %s", error)
        flash(error.user_message, "error")
        return redirect(next_url)
    flash(f"Portafolio «{name}» guardado.", "success")
    return redirect(url_for("account.portfolio_detail", portfolio_id=portfolio_id))


@bp.get("/portafolios")
@login_required
def portfolios():
    try:
        rows = repository.list_portfolios(*_context())
    except InsForgeError as error:
        flash(error.user_message, "error")
        rows = []
    return render_template("account/portfolios.html", portfolios=rows, model_labels=MODEL_LABELS)


@bp.get("/portafolios/<portfolio_id>")
@login_required
def portfolio_detail(portfolio_id: str):
    try:
        portfolio = repository.get_portfolio(*_context(), _checked_id(portfolio_id))
    except InsForgeError as error:
        flash(error.user_message, "error")
        return redirect(url_for("account.portfolios"))
    if portfolio is None:
        abort(404)
    return render_template(
        "account/portfolio_detail.html", portfolio=portfolio, model_labels=MODEL_LABELS
    )


@bp.post("/portafolios/<portfolio_id>/renombrar")
@login_required
def rename_portfolio(portfolio_id: str):
    portfolio_id = _checked_id(portfolio_id)
    try:
        name = _name_field()
        if not repository.rename_portfolio(*_context(), portfolio_id, name):
            abort(404)
        flash("Nombre actualizado.", "success")
    except ValueError as error:
        flash(str(error), "error")
    except InsForgeError as error:
        flash(error.user_message, "error")
    return redirect(url_for("account.portfolio_detail", portfolio_id=portfolio_id))


@bp.post("/portafolios/<portfolio_id>/eliminar")
@login_required
def delete_portfolio(portfolio_id: str):
    try:
        if repository.delete_portfolio(*_context(), _checked_id(portfolio_id)):
            flash("Portafolio eliminado.", "info")
    except InsForgeError as error:
        flash(error.user_message, "error")
    return redirect(url_for("account.portfolios"))


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
