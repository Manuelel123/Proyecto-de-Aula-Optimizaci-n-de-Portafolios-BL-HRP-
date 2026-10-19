"""Decision log: record every generated portfolio and show "Mi historial"."""

from flask import Blueprint

bp = Blueprint("decisions", __name__, template_folder="templates")

from optimizacion_portafolios.web.decisions import routes  # noqa: E402,F401
