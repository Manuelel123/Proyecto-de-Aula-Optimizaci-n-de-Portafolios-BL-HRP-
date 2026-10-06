"""Main dashboard application module."""

from flask import Blueprint

bp = Blueprint("main", __name__, template_folder="templates")

from optimizacion_portafolios.app.main import routes
