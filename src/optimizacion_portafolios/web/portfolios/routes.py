"""Mis portafolios: fix, list and track portfolios (sign-in required)."""

from flask import abort, flash, redirect, render_template, request, url_for

from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.web.auth.session import current_user_id, login_required
from optimizacion_portafolios.web.common.forms import normalize_ticker, parse_bounded_float
from optimizacion_portafolios.web.portfolios import bp, services
from optimizacion_portafolios.web.portfolios.snapshots import unsign

_NAME_MAX = 120


def owned_portfolio(portfolio_id: int):
    """The signed-in user's portfolio, or 404 (also when it belongs to someone else)."""
    portfolio = repositories.get_portfolio(current_user_id(), portfolio_id)
    if portfolio is None:
        abort(404)
    return portfolio


@bp.get("/")
@login_required
def index():
    return render_template(
        "portfolios/index.html", portfolios=services.portfolio_list(current_user_id())
    )


@bp.post("/fijar")
@login_required
def fix():
    """Two steps: ``step=form`` shows the form, ``step=confirm`` stores it."""
    token = request.form.get("snapshot", "")
    snapshot = unsign(token)
    if snapshot is None:
        flash("El resultado expiró o no es válido. Vuelve a calcular el portafolio.", "error")
        return redirect(url_for("portfolios.index"))
    form = services.fix_form(snapshot)
    values = {
        "name": form["default_name"],
        "capital": f"{form['capital']:.0f}",
        "benchmark": form["benchmark"],
        "commission_bps": f"{form['commission_bps']:g}",
    }
    if request.form.get("step") == "confirm":
        values = {field: request.form.get(field, values[field]).strip() for field in values}
        try:
            name = " ".join(values["name"].split())[:_NAME_MAX] or form["default_name"]
            capital = parse_bounded_float(
                "capital", values["capital"], 100, 1e12,
                "El capital inicial debe ser numérico.",
                "El capital inicial debe estar entre 100 y 1 000 000 000 000 USD.",
            )
            commission = parse_bounded_float(
                "commission_bps", values["commission_bps"], 0, 500,
                "La comisión debe ser numérica.",
                "La comisión debe estar entre 0 y 500 puntos básicos.",
            )
            benchmark = normalize_ticker(values["benchmark"])
            portfolio = services.fix_portfolio(
                current_user_id(), snapshot, name=name, capital=capital,
                benchmark=benchmark, commission_bps=commission,
            )
        except ValueError as error:
            flash(str(error), "error")
        else:
            flash(f"«{portfolio.name}» quedó fijado como portafolio óptimo. El seguimiento empieza el {portfolio.tracking_start.isoformat()}.", "success")
            return redirect(url_for("portfolios.detail", portfolio_id=portfolio.id))
    return render_template("portfolios/fix.html", form=form, values=values, snapshot=token)


@bp.get("/<int:portfolio_id>")
@login_required
def detail(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    return render_template("portfolios/detail.html", portfolio=services.portfolio_detail(portfolio))
