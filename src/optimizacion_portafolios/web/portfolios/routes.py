"""Mis portafolios: list and dashboard of tracked portfolios (sign-in required)."""

from flask import abort, render_template

from optimizacion_portafolios.storage import repositories
from optimizacion_portafolios.web.auth.session import current_user_id, login_required
from optimizacion_portafolios.web.portfolios import bp, services


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


@bp.get("/<int:portfolio_id>")
@login_required
def detail(portfolio_id: int):
    portfolio = owned_portfolio(portfolio_id)
    return render_template("portfolios/detail.html", portfolio=services.portfolio_detail(portfolio))
