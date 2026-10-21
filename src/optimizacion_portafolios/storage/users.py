"""Local user accounts: lookup, creation and ownership transfer.

Password hashing lives in ``web.auth.passwords``; this module only stores the
hash it is given. Emails are normalized (trimmed, lower case) before every
read and write, so the email is a case-insensitive username.
"""

from __future__ import annotations

from sqlalchemy import select, update

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import (
    DEFAULT_ROLE,
    ROLES,
    BacktestRun,
    Portfolio,
    User,
)


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def get_user(user_id: int | str) -> User | None:
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def get_user_by_email(email: str) -> User | None:
    return db.session.scalar(select(User).where(User.email == normalize_email(email)))


def create_user(
    email: str,
    password_hash: str,
    *,
    display_name: str | None = None,
    role: str = DEFAULT_ROLE,
    role_confirmed: bool = False,
) -> User:
    """Add (and commit) a user; raises ``ValueError`` when the email exists."""
    if role not in ROLES:
        raise ValueError(f"Rol desconocido: {role}")
    email = normalize_email(email)
    if get_user_by_email(email) is not None:
        raise ValueError("Ya existe una cuenta con ese correo.")
    user = User(
        email=email,
        password_hash=password_hash,
        display_name=(display_name or "").strip() or None,
        role=role,
        role_confirmed=role_confirmed,
    )
    db.session.add(user)
    db.session.commit()
    return user


def reassign_owner(old_user_id: str, user: User) -> dict[str, int]:
    """Move every row owned by ``old_user_id`` to ``user``; return counts per table.

    Only ``portfolios`` and ``backtest_runs`` carry ``user_id``; versions,
    holdings, transactions, snapshots, alerts and backtest results hang from
    them through foreign keys and follow automatically.
    """
    new_id = str(user.id)
    counts = {}
    for model in (Portfolio, BacktestRun):
        result = db.session.execute(
            update(model).where(model.user_id == old_user_id).values(user_id=new_id)
        )
        counts[model.__tablename__] = result.rowcount or 0
    db.session.commit()
    return counts
