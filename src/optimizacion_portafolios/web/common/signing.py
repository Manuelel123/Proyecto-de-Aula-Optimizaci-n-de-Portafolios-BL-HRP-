"""Signed, time-limited tokens (itsdangerous) keyed by the app's secret key."""

from flask import current_app
from itsdangerous import URLSafeTimedSerializer


def serializer(salt: str) -> URLSafeTimedSerializer:
    """Serializer of one token kind (each kind uses its own ``salt``)."""
    return URLSafeTimedSerializer(current_app.secret_key, salt=salt)
