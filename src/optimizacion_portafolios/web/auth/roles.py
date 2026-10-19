"""Role helpers shared by the auth flow and the role-gated pages.

``gestor`` is never chosen in a form: it comes only from ``GESTOR_EMAILS``
(app config, read from the environment variable of the same name), a
comma-separated list of emails compared case- and space-insensitively.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from flask import current_app, has_app_context

from optimizacion_portafolios.storage.models import DEFAULT_ROLE, ROLES, User
from optimizacion_portafolios.storage.users import normalize_email

GESTOR_ROLE = "gestor"
# Roles a user may pick (or be assigned) through the interface.
SELECTABLE_ROLES = tuple(role for role in ROLES if role != GESTOR_ROLE)


def parse_emails(raw: str | Iterable[str] | None) -> frozenset[str]:
    """Normalized emails of a comma-separated string (or an iterable of them)."""
    if raw is None:
        return frozenset()
    items = raw.split(",") if isinstance(raw, str) else raw
    return frozenset(email for email in (normalize_email(item) for item in items) if email)


def gestor_emails() -> frozenset[str]:
    """Emails granted the ``gestor`` role (app config, else the environment)."""
    if has_app_context():
        raw = current_app.config.get("GESTOR_EMAILS")
    else:
        raw = os.environ.get("GESTOR_EMAILS")
    return parse_emails(raw)


def is_gestor_email(email: str | None) -> bool:
    return normalize_email(email) in gestor_emails()


def initial_role(email: str) -> tuple[str, bool]:
    """``(role, role_confirmed)`` of a new account."""
    if is_gestor_email(email):
        return GESTOR_ROLE, True
    return DEFAULT_ROLE, False


def sync_gestor_role(user: User) -> bool:
    """Grant or revoke ``gestor`` according to ``GESTOR_EMAILS``.

    A listed email becomes a confirmed ``gestor``; a ``gestor`` no longer
    listed goes back to an unconfirmed ``explorador`` (so the interface asks
    them to confirm a role). Returns whether the user changed; the caller
    commits.
    """
    listed = is_gestor_email(user.email)
    if listed and (user.role != GESTOR_ROLE or not user.role_confirmed):
        user.role, user.role_confirmed = GESTOR_ROLE, True
        return True
    if not listed and user.role == GESTOR_ROLE:
        user.role, user.role_confirmed = DEFAULT_ROLE, False
        return True
    return False
