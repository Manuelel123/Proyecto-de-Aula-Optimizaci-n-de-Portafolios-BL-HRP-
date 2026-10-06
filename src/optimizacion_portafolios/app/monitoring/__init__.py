"""Asset monitoring application module."""

from flask import Blueprint

bp = Blueprint("monitoring", __name__, template_folder="templates")

from optimizacion_portafolios.app.monitoring import routes
