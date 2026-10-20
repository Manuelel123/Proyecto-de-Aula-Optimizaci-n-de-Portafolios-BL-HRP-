"""Flask application factory and shared application configuration."""

import hmac
import logging
import os
import secrets

import matplotlib

# Charts are rendered server-side; select the non-interactive backend before
# any module (including QuantStats) imports pyplot.
matplotlib.use("Agg")

from datetime import datetime, timedelta, timezone

from flask import Flask, abort, render_template, request, session

logger = logging.getLogger(__name__)


def create_app(test_config: dict | None = None) -> Flask:
    testing = bool(test_config and test_config.get("TESTING"))
    if not testing:
        # Local secrets (.env in the working directory) never override
        # variables already set.
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    # instance/ (local database) sits next to .env in the working directory.
    app = Flask(
        __name__,
        instance_relative_config=True,
        instance_path=os.path.abspath(os.environ.get("ATLAS_INSTANCE_PATH") or "instance"),
    )
    secure_cookies = os.environ.get("FLASK_SESSION_COOKIE_SECURE", "").lower() in {"1", "true"}
    secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not secret_key and not testing:
        logger.warning(
            "FLASK_SECRET_KEY is not set: using a random key, so sessions end on every "
            "restart and differ between workers. Set it in .env for any shared deployment."
        )
    app.config.from_mapping(
        SECRET_KEY=secret_key or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure_cookies,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        # Comma-separated emails that get the "gestor" role (web.auth.roles).
        GESTOR_EMAILS="" if testing else os.environ.get("GESTOR_EMAILS", ""),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL")
        or "sqlite:///" + os.path.join(app.instance_path, "atlas.db"),
    )
    if testing:
        # Tests get a private in-memory database unless they pass their own.
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
    if test_config:
        app.config.update(test_config)

    _configure_price_store(testing)
    _configure_storage(app, testing)
    _register_access_guard(app)
    _register_profile_requirement(app)
    _register_role_permissions(app)
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


def _configure_storage(app: Flask, testing: bool) -> None:
    """SQLite (or ``DATABASE_URL``) for users and their data, plus Flask-Login.

    Pending migrations are applied at start-up (disable with
    ``ATLAS_AUTO_MIGRATE=0``); tests create the tables directly.
    """
    from optimizacion_portafolios.storage import MIGRATIONS_DIRECTORY, db, migrate
    from optimizacion_portafolios.storage import models  # noqa: F401  (registers tables)
    from optimizacion_portafolios.web.auth.session import login_manager

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    migrate.init_app(app, db, directory=MIGRATIONS_DIRECTORY, render_as_batch=True)
    login_manager.init_app(app)
    with app.app_context():
        if testing:
            db.create_all()
        elif os.environ.get("ATLAS_AUTO_MIGRATE", "1") != "0" and os.path.isdir(
            os.path.join(MIGRATIONS_DIRECTORY, "versions")
        ):
            from flask_migrate import upgrade

            upgrade(directory=MIGRATIONS_DIRECTORY)


def _configure_price_store(testing: bool) -> None:
    """InsForge as the server-side price cache (optional; never for accounts).

    Reads ``INSFORGE_URL`` and ``INSFORGE_API_KEY``. Tests configure
    ``data.price_store`` themselves.
    """
    if testing:
        return
    from optimizacion_portafolios.data import price_store
    from optimizacion_portafolios.insforge import InsForgeClient, InsForgeSettings

    settings = InsForgeSettings.from_env()
    price_store.configure(
        InsForgeClient(settings) if settings else None,
        full_refresh_days=float(
            os.environ.get("ATLAS_PRICE_FULL_REFRESH_DAYS") or price_store.DEFAULT_FULL_REFRESH_DAYS
        ),
        intraday_ttl_minutes=float(
            os.environ.get("ATLAS_PRICE_INTRADAY_TTL_MINUTES")
            or price_store.DEFAULT_INTRADAY_TTL_MINUTES
        ),
    )


def _register_access_guard(app: Flask) -> None:
    """Session required everywhere except the public endpoints (``web.auth.guard``).

    Registered before the CSRF check, so anonymous requests are redirected
    (or get a 401 JSON) without touching any view.
    """
    from optimizacion_portafolios.web.auth import guard

    guard.init_app(app)


def _register_profile_requirement(app: Flask) -> None:
    """No tool without a current risk profile (``web.profile.access``).

    Registers the ``require_current_profile`` access check (first, before any
    role check), the after-sign-in hook that fills ``session["perfil"]`` and
    the ``current_profile`` template variable. Loads and validates
    ``config/cuestionario.yaml`` (or ``QUESTIONNAIRE_PATH``) at start-up.
    """
    from optimizacion_portafolios.web.profile import access

    access.init_app(app)


def _register_role_permissions(app: Flask) -> None:
    """Pages and actions by role (``web.auth.permissions``).

    Registers the ``require_role`` access check (after the profile check) and
    the ``can`` / ``can_open`` / ``nav_items`` template helpers.
    """
    from optimizacion_portafolios.web.auth import permissions

    permissions.init_app(app)


def _register_template_helpers(app: Flask) -> None:
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
        if isinstance(value, datetime):
            moment = value
        else:
            try:
                moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return value
        if moment.tzinfo is None:
            # SQLite drops the offset; stored timestamps are UTC.
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _register_blueprints(app: Flask) -> None:
    from optimizacion_portafolios.web.account import bp as account_bp
    from optimizacion_portafolios.web.auth import bp as auth_bp
    from optimizacion_portafolios.web.portfolios import bp as portfolios_bp
    from optimizacion_portafolios.web.black_litterman import bp as black_litterman_bp
    from optimizacion_portafolios.web.compare import bp as compare_bp
    from optimizacion_portafolios.web.decisions import bp as decisions_bp
    from optimizacion_portafolios.web.history import bp as history_bp
    from optimizacion_portafolios.web.hrp import bp as hrp_bp
    from optimizacion_portafolios.web.main import bp as main_bp
    from optimizacion_portafolios.web.monitoring import bp as monitoring_bp
    from optimizacion_portafolios.web.profile import bp as profile_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(hrp_bp)
    app.register_blueprint(black_litterman_bp)
    app.register_blueprint(compare_bp)
    app.register_blueprint(decisions_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(portfolios_bp)
    app.register_blueprint(profile_bp)


def _register_csrf_protection(app: Flask) -> None:
    @app.context_processor
    def inject_csrf_token():
        from optimizacion_portafolios.web.common.notice import EDUCATIONAL_NOTICE

        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf_token"], "educational_notice_text": EDUCATIONAL_NOTICE}

    @app.before_request
    def validate_csrf_token():
        if request.method != "POST":
            return
        expected = session.get("csrf_token")
        # No token in the session (e.g. a cross-site POST to the sign-in form)
        # is rejected too: comparing two empty strings would succeed.
        if not expected or not hmac.compare_digest(expected, request.form.get("csrf_token", "")):
            abort(400, description="La sesión del formulario expiró. Vuelve a cargarlo.")


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(error):
        return render_template(
            "error.html", status=400, message=error.description
        ), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template(
            "error.html", status=403, message=error.description
        ), 403

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
