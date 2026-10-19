"""Global access guard: every endpoint requires a session unless it is public.

``require_session`` runs before every request (registered by ``init_app``):

1. Public endpoints (``PUBLIC_ENDPOINTS`` plus any ``static``) pass through.
2. Anonymous requests get ``unauthorized_response()``: a redirect to sign in
   with ``next`` for pages, a 401 JSON (no data) for ``/api/*``.
3. Signed-in requests then go through the access checks registered with
   ``register_access_check(app, check)``, in registration order, except for
   the endpoints in ``CHECK_EXEMPT_ENDPOINTS`` (e.g. signing out). A check is
   a function ``check(endpoint: str) -> Response | None``; the first non-None
   result is returned instead of the view. This is the extension point for
   "the profile must be current" and "the role must allow this page".
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Flask, current_app, request, session
from flask_login import current_user

from optimizacion_portafolios.web.auth.session import LEGACY_SESSION_KEYS, unauthorized_response

# Endpoints reachable without a session. Keep it minimal.
PUBLIC_ENDPOINTS: set[str] = {"main.index", "auth.login", "auth.register", "static"}
# Endpoints that need a session but skip the registered access checks.
CHECK_EXEMPT_ENDPOINTS: set[str] = {"auth.logout"}

AccessCheck = Callable[[str], object | None]
_EXTENSION_KEY = "access_checks"


def is_public_endpoint(endpoint: str | None) -> bool:
    if endpoint is None:
        # No matching route: let Flask answer 404/405 (no content leaks).
        return True
    return endpoint in PUBLIC_ENDPOINTS or endpoint == "static" or endpoint.endswith(".static")


def register_access_check(app: Flask, check: AccessCheck) -> AccessCheck:
    """Add ``check`` to the checks run for signed-in, non-public requests."""
    app.extensions.setdefault(_EXTENSION_KEY, []).append(check)
    return check


def require_session():
    for key in LEGACY_SESSION_KEYS:
        session.pop(key, None)
    endpoint = request.endpoint
    if is_public_endpoint(endpoint):
        return None
    if not current_user.is_authenticated:
        return unauthorized_response()
    if endpoint in CHECK_EXEMPT_ENDPOINTS:
        return None
    for check in current_app.extensions.get(_EXTENSION_KEY, []):
        response = check(endpoint)
        if response is not None:
            return response
    return None


def init_app(app: Flask) -> None:
    app.extensions.setdefault(_EXTENSION_KEY, [])
    app.before_request(require_session)
