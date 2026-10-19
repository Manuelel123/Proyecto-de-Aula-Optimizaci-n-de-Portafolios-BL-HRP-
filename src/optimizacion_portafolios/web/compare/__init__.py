"""Side-by-side HRP vs Black-Litterman for the same assets (inversionista and gestor)."""

from flask import Blueprint

bp = Blueprint("compare", __name__, template_folder="templates")

from optimizacion_portafolios.web.compare import routes  # noqa: E402,F401
