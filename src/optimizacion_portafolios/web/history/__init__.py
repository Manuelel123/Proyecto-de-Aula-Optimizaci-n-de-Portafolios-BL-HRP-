"""On-demand full price history of a single asset (JSON for the price explorer)."""

from flask import Blueprint

bp = Blueprint("history", __name__)

from optimizacion_portafolios.web.history import routes
