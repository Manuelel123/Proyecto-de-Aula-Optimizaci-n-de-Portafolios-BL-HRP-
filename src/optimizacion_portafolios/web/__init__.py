"""Flask application factory and shared application configuration."""

import hmac
import os
import secrets

import matplotlib

# Charts are rendered server-side; select the non-interactive backend before
# any module (including QuantStats) imports pyplot.
matplotlib.use("Agg")

from datetime import datetime, timedelta

from flask import Flask, abort, render_template, request, session


def create_app(test_config: dict | None = None) -> Flask:
    testing = bool(test_config and test_config.get("TESTING"))
    if not testing:
        # Local secrets (.env in the working directory) never override
        # variables already set.
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
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
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    if test_config:
        app.config.update(test_config)

    _configure_insforge(app, testing)
    _register_blueprints(app)
    _register_template_helpers(app)
    _register_csrf_protection(app)
    _register_error_handlers(app)
    _warm_up_backtest_engine(app)
    return app


def _warm_up_backtest_engine(app: Flask) -> None:
    """Compile vectorbt's numba kernels in the background (not in tests).

    The first backtest otherwise pays the import and JIT compilation (several
    seconds). Disable with ``ATLAS_VECTORBT_WARMUP=0``.
    """
    if app.testing or os.environ.get("ATLAS_VECTORBT_WARMUP", "1") == "0":
        return
    from optimizacion_portafolios.models.hrp_backtest import warm_up_in_background

    warm_up_in_background()


def _configure_insforge(app: Flask, testing: bool) -> None:
    """InsForge client for accounts and the persistent price cache (optional).

    Reads ``INSFORGE_URL``, ``INSFORGE_ANON_KEY`` and ``INSFORGE_API_KEY``. Tests
    pass a client (or nothing) in ``app.config["INSFORGE_CLIENT"]`` instead.
    """
    from optimizacion_portafolios.data import price_store
    from optimizacion_portafolios.insforge import InsForgeClient, InsForgeSettings

    if testing:
        client = app.config.get("INSFORGE_CLIENT")
    else:
        settings = InsForgeSettings.from_env()
        client = InsForgeClient(settings) if settings else None
        ttl = float(os.environ.get("ATLAS_PRICE_DB_TTL_HOURS") or price_store.DEFAULT_TTL_HOURS)
        price_store.configure(client, ttl)
    if client is not None:
        app.extensions["insforge"] = client


def _register_template_helpers(app: Flask) -> None:
    from optimizacion_portafolios.web.auth.session import current_user

    @app.context_processor
    def inject_user():
        return {
            "current_user": current_user(),
            "accounts_enabled": "insforge" in app.extensions,
        }

    def _number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @app.template_filter("pct")
    def percent(value, decimals=2):
        number = _number(value)
        return "N/D" if number is None else f"{number * 100:.{decimals}f} %"

    @app.template_filter("ratio")
    def ratio(value):
        number = _number(value)
        return "N/D" if number is None else f"{number:.3f}"

    @app.template_filter("money")
    def money(value):
        number = _number(value)
        return "N/D" if number is None else f"${number:,.2f}"

    @app.template_filter("datetime_short")
    def datetime_short(value):
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
        except ValueError:
            return value
        return moment.strftime("%Y-%m-%d %H:%M")


def _register_blueprints(app: Flask) -> None:
    from optimizacion_portafolios.web.account import bp as account_bp
    from optimizacion_portafolios.web.auth import bp as auth_bp
    from optimizacion_portafolios.web.black_litterman import bp as black_litterman_bp
    from optimizacion_portafolios.web.history import bp as history_bp
    from optimizacion_portafolios.web.hrp import bp as hrp_bp
    from optimizacion_portafolios.web.main import bp as main_bp
    from optimizacion_portafolios.web.monitoring import bp as monitoring_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(hrp_bp)
    app.register_blueprint(black_litterman_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)


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
