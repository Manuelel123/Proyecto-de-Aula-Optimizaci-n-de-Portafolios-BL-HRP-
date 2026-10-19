"""Risk profiling: mandatory questionnaire, result, role confirmation and "Mi perfil"."""

from flask import Blueprint

bp = Blueprint("profile", __name__, url_prefix="/perfil", template_folder="templates")

from optimizacion_portafolios.web.profile import routes  # noqa: E402,F401
