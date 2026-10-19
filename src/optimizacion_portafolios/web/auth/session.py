"""Signed-in state with Flask-Login over the local ``users`` table.

The session cookie (signed, httpOnly) only carries Flask-Login's user id and
the CSRF token; ``current_user`` is loaded from the database on each request,
so role changes apply immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlsplit

import flask_login
from flask import flash, jsonify, redirect, request, session, url_for
from flask_login import LoginManager

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import User
from optimizacion_portafolios.storage.users import get_user

# Session keys left by the former InsForge sign-in; removed on sight.
LEGACY_SESSION_KEYS = ("auth", "auth_pending_email", "auth_reset_email", "auth_oauth")
LOGIN_MESSAGE = "Inicia sesión para continuar."

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = LOGIN_MESSAGE
login_manager.login_message_category = "info"
login_manager.session_protection = "basic"


@login_manager.user_loader
def _load_user(user_id: str) -> User | None:
    return get_user(user_id)


def wants_json() -> bool:
    """Whether the current request is for a JSON endpoint (``/api/...``)."""
    return request.path.startswith("/api/")


def unauthorized_response():
    """Anonymous access: 401 JSON for ``/api/*``, else a redirect to sign in."""
    if wants_json():
        return jsonify({"error": "Inicia sesión para continuar."}), 401
    flash(LOGIN_MESSAGE, "info")
    next_url = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=next_url))


login_manager.unauthorized_handler(unauthorized_response)


def login_user(user: User) -> None:
    """Start a fresh session for ``user`` (rotates the CSRF token)."""
    session.clear()
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()
    flask_login.login_user(user)
    session.permanent = True


def logout_user() -> None:
    flask_login.logout_user()
    session.clear()


def current_user_id() -> str | None:
    """``str(User.id)`` of the signed-in user (owner of local database rows)."""
    user = flask_login.current_user
    return str(user.id) if user.is_authenticated else None


def safe_next(target: str | None, default: str = "main.index") -> str:
    """Relative in-app redirect target (blocks open redirects)."""
    if target:
        parts = urlsplit(target)
        if (
            not parts.scheme
            and not parts.netloc
            and target.startswith("/")
            and not target.startswith("//")
            and "\\" not in target
        ):
            return target
    return url_for(default)


def login_required(view):
    """Redirect anonymous users to the sign-in page, then back to ``view``.

    The global guard (``web.auth.guard``) already enforces this for every
    non-public endpoint; the decorator documents it at the route.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not flask_login.current_user.is_authenticated:
            return unauthorized_response()
        return view(*args, **kwargs)

    return wrapped
