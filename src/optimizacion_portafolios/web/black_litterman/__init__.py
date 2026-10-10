"""Black-Litterman application module."""

from flask import Blueprint

bp = Blueprint("black_litterman", __name__, template_folder="templates")

from optimizacion_portafolios.web.black_litterman import routes
