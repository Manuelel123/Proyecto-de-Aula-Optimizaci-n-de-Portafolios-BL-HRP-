"""User accounts with InsForge Auth: email/password, email code, OAuth."""

from flask import Blueprint

bp = Blueprint("auth", __name__, url_prefix="/auth", template_folder="templates")

from optimizacion_portafolios.web.auth import routes
