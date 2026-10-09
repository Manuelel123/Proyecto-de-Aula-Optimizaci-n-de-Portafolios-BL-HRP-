"""Flask application factory and shared application configuration."""

import hmac
import os
import secrets

import matplotlib

# Charts are rendered server-side; select the non-interactive backend before
# any module (including QuantStats) imports pyplot.
matplotlib.use("Agg")

from flask import Flask, abort, render_template, request, session


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get(
            "FLASK_SESSION_COOKIE_SECURE", ""
        ).lower()
        in {"1", "true"},
    )
    if test_config:
        app.config.update(test_config)

    _register_blueprints(app)
    _register_csrf_protection(app)
    _register_error_handlers(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from optimizacion_portafolios.web.black_litterman import bp as black_litterman_bp
    from optimizacion_portafolios.web.hrp import bp as hrp_bp
    from optimizacion_portafolios.web.main import bp as main_bp
    from optimizacion_portafolios.web.monitoring import bp as monitoring_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(hrp_bp)
    app.register_blueprint(black_litterman_bp)


def _register_csrf_protection(app: Flask) -> None:
    @app.context_processor
    def inject_csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf_token"]}

    @app.before_request
    def validate_csrf_token():
        if request.method == "POST" and not hmac.compare_digest(
            session.get("csrf_token", ""),
            request.form.get("csrf_token", ""),
        ):
            abort(400, description="La sesión del formulario expiró. Vuelve a cargarlo.")


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(error):
        return render_template(
            "error.html", status=400, message=error.description
        ), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template(
            "error.html",
            status=404,
            message="La dirección solicitada no existe.",
        ), 404


def run() -> None:
    """Console entry point: ``uv run optimizacion-portafolios``."""
    create_app().run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true"},
    )
