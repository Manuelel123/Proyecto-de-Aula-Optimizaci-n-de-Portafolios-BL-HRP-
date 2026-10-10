"""Hierarchical risk parity application module."""

from flask import Blueprint

bp = Blueprint("hrp", __name__, template_folder="templates")

from optimizacion_portafolios.web.hrp import routes
