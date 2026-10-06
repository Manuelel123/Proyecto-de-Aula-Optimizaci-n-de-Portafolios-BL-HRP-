"""Flask application factory and web presentation layer."""

import hmac
import os
import secrets

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

    from optimizacion_portafolios.web.black_litterman import black_litterman_blueprint
    from optimizacion_portafolios.web.hrp import hrp_blueprint
    from optimizacion_portafolios.web.main import main_blueprint
    from optimizacion_portafolios.web.monitoring import monitoring_blueprint

    app.register_blueprint(main_blueprint)
    app.register_blueprint(monitoring_blueprint)
    app.register_blueprint(hrp_blueprint)
    app.register_blueprint(black_litterman_blueprint)

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

    @app.errorhandler(400)
    def bad_request(_error):
        return render_template(
            "error.html", status=400, message=_error.description
        ), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template(
            "error.html",
            status=404,
            message="La dirección solicitada no existe.",
        ), 404

    return app


def run() -> None:
    create_app().run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true"},
    )
