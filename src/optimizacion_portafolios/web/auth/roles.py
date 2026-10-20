"""Role helpers shared by the auth flow and the role-gated pages.

``gestor`` is never chosen in a form nor derived from configuration: it is
granted only from the server's command line (``flask users make-gestor``),
to a single account with a verified email. The database is the source of
truth for every role.
"""

from __future__ import annotations

from sqlalchemy import select

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import DEFAULT_ROLE, ROLES, User

GESTOR_ROLE = "gestor"
# Roles a user may pick (or be assigned) through the interface.
SELECTABLE_ROLES = tuple(role for role in ROLES if role != GESTOR_ROLE)

# Earlier role names still present in historical rows (perfiles, decisions).
LEGACY_ROLES = {"explorador": DEFAULT_ROLE}


def canonical_role(role: str | None) -> str | None:
    """Current name of ``role`` (maps the legacy ``explorador`` to ``novato``)."""
    return LEGACY_ROLES.get(role, role) if role else role


def current_gestors() -> list[User]:
    return list(db.session.scalars(select(User).where(User.role == GESTOR_ROLE)))


def grant_gestor(user: User, *, replace: bool = False) -> list[User]:
    """Make ``user`` the gestor; returns the accounts that stopped being gestor.

    There is a single gestor: another gestor blocks the grant unless
    ``replace`` is set, in which case it goes back to an unconfirmed novato.
    The caller commits.
    """
    others = [other for other in current_gestors() if other.id != user.id]
    if others and not replace:
        raise ValueError(
            "Ya hay una cuenta gestor (" + ", ".join(other.email for other in others) + "). "
            "Usa --replace para reemplazarla."
        )
    for other in others:
        other.role, other.role_confirmed = DEFAULT_ROLE, False
    user.role, user.role_confirmed = GESTOR_ROLE, True
    return others


def revoke_gestor(user: User) -> None:
    """``user`` goes back to an unconfirmed novato (asked to confirm a role)."""
    user.role, user.role_confirmed = DEFAULT_ROLE, False
