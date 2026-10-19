"""Tracked ("optimal") portfolios of the signed-in user."""

from flask import Blueprint

bp = Blueprint("portfolios", __name__, url_prefix="/portafolios", template_folder="templates")

from optimizacion_portafolios.web.portfolios import routes
