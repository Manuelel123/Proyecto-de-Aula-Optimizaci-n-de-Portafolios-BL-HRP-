"""Local user accounts: email + password (argon2) in SQLite, with Flask-Login."""

from flask import Blueprint

bp = Blueprint(
    "auth", __name__, url_prefix="/auth", template_folder="templates", cli_group="users"
)

from optimizacion_portafolios.web.auth import cli, routes
