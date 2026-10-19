"""Signed-in user state kept in the Flask session (InsForge server tokens).

The session cookie is signed and httpOnly; it stores the InsForge access token,
the refresh token (``client_type=server`` returns it in the response body) and
a small user summary. Access tokens are refreshed shortly before they expire,
and refresh tokens rotate on every refresh.

Flask-Login manages the signed-in state on top of it: ``current_user`` (in
views and templates) is an ``AccountUser`` built from that summary, whose id is
the InsForge user id that owns the rows of the local database.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from functools import wraps
from urllib.parse import urlsplit

import flask_login
from flask import current_app, flash, redirect, request, session, url_for
from flask_login import LoginManager, UserMixin

from optimizacion_portafolios.insforge import InsForgeClient, InsForgeError

logger = logging.getLogger(__name__)

_SESSION_KEY = "auth"
_REFRESH_MARGIN_SECONDS = 60

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "info"
login_manager.session_protection = "basic"


class AccountUser(UserMixin):
    """The signed-in InsForge account (``id`` is the InsForge user id)."""

    def __init__(self, summary: dict):
        self.id = summary["id"]
        self.email = summary.get("email") or ""
        self.name = summary.get("name") or self.email

    def get_id(self) -> str:
        return self.id


@login_manager.user_loader
def _load_user(user_id: str) -> AccountUser | None:
    state = session.get(_SESSION_KEY) or {}
    summary = state.get("user") or {}
    return AccountUser(summary) if summary.get("id") == user_id else None


def insforge_client() -> InsForgeClient | None:
    """The app's InsForge client, or ``None`` when accounts are not configured."""
    return current_app.extensions.get("insforge")


def _token_expiry(token: str) -> float | None:
    """``exp`` claim of a JWT (not verified: InsForge verifies it on every call)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _user_summary(user: dict) -> dict:
    profile = user.get("profile") or {}
    email = user.get("email") or ""
    return {
        "id": user.get("id"),
        "email": email,
        "name": profile.get("name") or user.get("name") or email.split("@")[0],
    }


def login_user(auth_response: dict) -> None:
    """Store the tokens of a sign-in, verification, OAuth or refresh response."""
    access = auth_response.get("accessToken")
    refresh = auth_response.get("refreshToken")
    if not access or not refresh:
        raise InsForgeError("El servidor de cuentas no devolvió una sesión.")
    previous_user = (session.get(_SESSION_KEY) or {}).get("user") or {}
    user = auth_response.get("user")
    summary = _user_summary(user) if user else previous_user
    session[_SESSION_KEY] = {"access": access, "refresh": refresh, "user": summary}
    session.permanent = True
    if summary.get("id"):
        flask_login.login_user(AccountUser(summary))


def logout_user() -> None:
    session.pop(_SESSION_KEY, None)
    flask_login.logout_user()


def current_user_id() -> str | None:
    """InsForge id of the signed-in user (owner of local database rows)."""
    user = flask_login.current_user
    return user.get_id() if user.is_authenticated else None


def current_user() -> dict | None:
    state = session.get(_SESSION_KEY)
    return state.get("user") if state else None


def access_token() -> str | None:
    """A valid access token for the signed-in user, refreshing it if needed.

    Returns ``None`` (and signs the user out) when the refresh token is no
    longer accepted.
    """
    state = session.get(_SESSION_KEY)
    if not state:
        return None
    expiry = _token_expiry(state["access"])
    if expiry is not None and expiry - time.time() > _REFRESH_MARGIN_SECONDS:
        return state["access"]
    client = insforge_client()
    if client is None:
        return None
    try:
        login_user(client.refresh(state["refresh"]))
    except InsForgeError as error:
        if error.status is None:
            # Network failure: keep the session and let the caller report it.
            raise
        logger.info("InsForge refresh rejected (%s); signing out", error.status)
        logout_user()
        return None
    return session[_SESSION_KEY]["access"]


def safe_next(target: str | None, default: str = "main.index") -> str:
    """Relative in-app redirect target (blocks open redirects)."""
    if target:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return url_for(default)


def login_required(view):
    """Redirect anonymous users to the sign-in page, then back to ``view``."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if insforge_client() is None:
            flash("Las cuentas de usuario no están configuradas en este servidor.", "warning")
            return redirect(url_for("main.index"))
        try:
            token = access_token()
        except InsForgeError as error:
            flash(error.user_message, "error")
            return redirect(url_for("main.index"))
        if token is None:
            flash("Inicia sesión para continuar.", "info")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped
