"""One-time email codes (account verification and password reset).

A code is 6 random digits, stored only as an HMAC-SHA256 keyed with the app
secret. Issuing a code replaces any open code of the same purpose; a code is
valid for ``CODE_TTL``, can be used once and is burned after
``MAX_ATTEMPTS`` wrong tries. ``RESEND_COOLDOWN`` limits how often a new one
can be sent.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import EmailCode

PURPOSES = ("verify", "reset")
CODE_TTL = timedelta(minutes=15)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime) -> datetime:
    """SQLite returns naive datetimes: they were stored in UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _digest(secret: str, code: str) -> str:
    return hmac.new(secret.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()


def _open_code(user_id: int, purpose: str) -> EmailCode | None:
    return db.session.scalar(
        select(EmailCode)
        .where(EmailCode.user_id == user_id, EmailCode.purpose == purpose, EmailCode.used_at.is_(None))
        .order_by(EmailCode.created_at.desc())
    )


def seconds_until_resend(user_id: int, purpose: str) -> int:
    """Seconds before another code may be sent (0 when allowed)."""
    code = _open_code(user_id, purpose)
    if code is None:
        return 0
    remaining = (_aware(code.created_at) + RESEND_COOLDOWN - _now()).total_seconds()
    return int(remaining) + 1 if remaining > 0 else 0


def issue_code(user_id: int, purpose: str, secret: str) -> str:
    """New code for ``user_id`` (commits); previous codes of the purpose are dropped."""
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown code purpose: {purpose}")
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.session.execute(delete(EmailCode).where(EmailCode.user_id == user_id, EmailCode.purpose == purpose))
    now = _now()
    db.session.add(
        EmailCode(user_id=user_id, purpose=purpose, code_hash=_digest(secret, code), created_at=now, expires_at=now + CODE_TTL)
    )
    db.session.commit()
    return code


def check_code(user_id: int, purpose: str, code: str, secret: str) -> bool:
    """Consume ``code`` if it is right and still valid (commits either way)."""
    stored = _open_code(user_id, purpose)
    if stored is None or _aware(stored.expires_at) < _now() or stored.attempts >= MAX_ATTEMPTS:
        return False
    if hmac.compare_digest(stored.code_hash, _digest(secret, (code or "").strip())):
        stored.used_at = _now()
        db.session.commit()
        return True
    stored.attempts += 1
    db.session.commit()
    return False
