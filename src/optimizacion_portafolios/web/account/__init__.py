"""Signed-in user area: saved portfolios and backtest history (InsForge)."""

from flask import Blueprint

bp = Blueprint("account", __name__, url_prefix="/cuenta", template_folder="templates")

from optimizacion_portafolios.web.account import routes
