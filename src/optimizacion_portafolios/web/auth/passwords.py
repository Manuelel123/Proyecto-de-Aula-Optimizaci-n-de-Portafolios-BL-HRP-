"""Password policy and argon2id hashing (argon2-cffi).

Plain-text passwords only live in the request that carries them: they are
never stored, logged, flashed or put in exception messages.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 8
MAX_LENGTH = 128

_hasher = PasswordHasher()
# Verified when the email has no account, so a miss costs as much as a hit.
_DUMMY_HASH = _hasher.hash("atlas-dummy-password")


def password_rules() -> list[str]:
    """Human-readable policy (Spanish) for the registration form."""
    return [f"al menos {MIN_LENGTH} caracteres", "letras y números"]


def password_errors(password: str, email: str = "") -> list[str]:
    """Policy violations of a new password (empty when it is acceptable)."""
    errors = []
    if len(password) < MIN_LENGTH:
        errors.append(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres.")
    if len(password) > MAX_LENGTH:
        errors.append(f"La contraseña no puede superar {MAX_LENGTH} caracteres.")
    if not any(character.isalpha() for character in password) or not any(
        character.isdigit() for character in password
    ):
        errors.append("La contraseña debe combinar letras y números.")
    if email and password.strip().lower() == email.strip().lower():
        errors.append("La contraseña no puede ser igual al correo.")
    return errors


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """``True`` when ``password`` matches; never raises for a bad hash."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether the hash was made with weaker parameters than the current ones."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True
