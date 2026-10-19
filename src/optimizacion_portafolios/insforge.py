"""Minimal InsForge REST client (auth and database) for the Flask server.

The official SDK is JavaScript-only, so the server talks to the documented REST
API with ``requests``. Auth calls use ``client_type=server``: the refresh token
comes back in the response body and the server keeps it in the user's session.

Database calls take a bearer token: the signed-in user's access token for
user-scoped tables (RLS decides which rows they see) or the project API key for
server-only tables (``admin=True``). The API key is a full-access admin key and
never leaves the server.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
# PostgREST caps responses; reads page through results in chunks of this size.
PAGE_SIZE = 1000
_PUBLIC_CONFIG_TTL = 300

# Spanish messages for the error codes the UI can trigger. The live API
# answers AUTH_UNAUTHORIZED / AUTH_EMAIL_EXISTS / FORBIDDEN where the docs
# list INVALID_CREDENTIALS / USER_EXISTS / EMAIL_NOT_VERIFIED; both are mapped.
_EXISTS = "Ya existe una cuenta con ese correo. Inicia sesión."
_ERROR_MESSAGES = {
    "INVALID_CREDENTIALS": "Correo o contraseña incorrectos.",
    "USER_EXISTS": _EXISTS,
    "AUTH_EMAIL_EXISTS": _EXISTS,
    "EMAIL_NOT_VERIFIED": "Debes verificar tu correo antes de iniciar sesión.",
}


class InsForgeError(Exception):
    """An InsForge request failed (HTTP error, network error or bad response)."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def user_message(self) -> str:
        """Message to show in the interface (Spanish)."""
        if self.code in _ERROR_MESSAGES:
            return _ERROR_MESSAGES[self.code]
        if self.status == 429:
            return "Demasiados intentos. Espera un momento y vuelve a intentar."
        if self.status is None:
            return "No se pudo conectar con el servidor de cuentas. Intenta de nuevo."
        return f"No se pudo completar la solicitud: {self}"


@dataclass(frozen=True)
class InsForgeSettings:
    base_url: str
    anon_key: str
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "InsForgeSettings | None":
        base_url = os.environ.get("INSFORGE_URL", "").strip()
        anon_key = os.environ.get("INSFORGE_ANON_KEY", "").strip()
        if not base_url or not anon_key:
            return None
        api_key = os.environ.get("INSFORGE_API_KEY", "").strip() or None
        return cls(base_url.rstrip("/"), anon_key, api_key)


def in_filter(values) -> str:
    """PostgREST ``in.(...)`` filter with each value double-quoted."""
    quoted = ",".join('"' + str(value).replace('"', '\\"') + '"' for value in values)
    return f"in.({quoted})"


class InsForgeClient:
    def __init__(
        self,
        settings: InsForgeSettings,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ):
        self.settings = settings
        self.timeout = timeout
        self._http = session or requests.Session()
        self._public_config: tuple[float, dict] | None = None
        self._config_lock = Lock()

    @property
    def has_admin_key(self) -> bool:
        return bool(self.settings.api_key)

    # ------------------------------------------------------------------ HTTP
    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict | None = None,
        json: Any = None,
        headers: dict | None = None,
    ) -> Any:
        request_headers = {"Authorization": f"Bearer {token or self.settings.anon_key}"}
        if json is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        try:
            response = self._http.request(
                method,
                self.settings.base_url + path,
                params=params,
                json=json,
                headers=request_headers,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise InsForgeError(str(error)) from error
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            message = body.get("message") or body.get("error") or response.reason
            raise InsForgeError(str(message), response.status_code, body.get("error"))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise InsForgeError("Respuesta no válida del servidor.", response.status_code) from error

    # ------------------------------------------------------------------ Auth
    def public_config(self) -> dict:
        """Public auth settings (OAuth providers, password policy), cached 5 min."""
        with self._config_lock:
            if self._public_config and monotonic() - self._public_config[0] < _PUBLIC_CONFIG_TTL:
                return self._public_config[1]
        config = self._request("GET", "/api/auth/public-config") or {}
        with self._config_lock:
            self._public_config = (monotonic(), config)
        return config

    def sign_up(self, email: str, password: str, name: str | None = None) -> dict:
        body = {"email": email, "password": password}
        if name:
            body["name"] = name
        return self._request(
            "POST", "/api/auth/users", params={"client_type": "server"}, json=body
        )

    def sign_in(self, email: str, password: str) -> dict:
        return self._request(
            "POST",
            "/api/auth/sessions",
            params={"client_type": "server"},
            json={"method": "password", "email": email, "password": password},
        )

    def send_verification(self, email: str) -> None:
        self._request("POST", "/api/auth/email/send-verification", json={"email": email})

    def verify_email(self, email: str, otp: str) -> dict:
        return self._request(
            "POST",
            "/api/auth/email/verify",
            params={"client_type": "server"},
            json={"email": email, "otp": otp},
        )

    def refresh(self, refresh_token: str) -> dict:
        return self._request(
            "POST",
            "/api/auth/refresh",
            params={"client_type": "server"},
            json={"refreshToken": refresh_token},
        )

    def sign_out(self, access_token: str) -> None:
        self._request("POST", "/api/auth/logout", token=access_token)

    def oauth_url(self, provider: str, redirect_uri: str, code_challenge: str) -> str:
        data = self._request(
            "GET",
            f"/api/auth/oauth/{quote(provider, safe='')}",
            params={"redirect_uri": redirect_uri, "code_challenge": code_challenge},
        )
        url = (data or {}).get("authUrl")
        if not url:
            raise InsForgeError("El proveedor no devolvió una URL de acceso.")
        return url

    def oauth_exchange(self, code: str, code_verifier: str) -> dict:
        return self._request(
            "POST",
            "/api/auth/oauth/exchange",
            params={"client_type": "server"},
            json={"code": code, "code_verifier": code_verifier},
        )

    def send_password_reset(self, email: str) -> None:
        self._request(
            "POST", "/api/auth/email/send-reset-password", json={"email": email}
        )

    def exchange_reset_code(self, email: str, code: str) -> str:
        data = self._request(
            "POST",
            "/api/auth/email/exchange-reset-password-token",
            json={"email": email, "code": code},
        )
        token = (data or {}).get("token")
        if not token:
            raise InsForgeError("El código de recuperación no es válido.")
        return token

    def reset_password(self, reset_token: str, new_password: str) -> None:
        self._request(
            "POST",
            "/api/auth/email/reset-password",
            json={"newPassword": new_password, "otp": reset_token},
        )

    # -------------------------------------------------------------- Database
    def _db_token(self, token: str | None, admin: bool) -> str:
        if admin:
            if not self.settings.api_key:
                raise InsForgeError("Falta INSFORGE_API_KEY para operaciones del servidor.")
            return self.settings.api_key
        if not token:
            raise InsForgeError("Se requiere una sesión iniciada.", 401)
        return token

    def select(
        self,
        table: str,
        params: dict | None = None,
        *,
        token: str | None = None,
        admin: bool = False,
        limit: int | None = None,
    ) -> list[dict]:
        """Rows of ``table`` matching PostgREST ``params``, paging past the cap."""
        bearer = self._db_token(token, admin)
        rows: list[dict] = []
        offset = 0
        while True:
            page = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(rows))
            query = {**(params or {}), "limit": page, "offset": offset}
            batch = self._request(
                "GET", f"/api/database/records/{table}", token=bearer, params=query
            ) or []
            rows.extend(batch)
            if len(batch) < page or (limit is not None and len(rows) >= limit):
                return rows
            offset += page

    def insert(
        self,
        table: str,
        rows: list[dict],
        *,
        token: str | None = None,
        admin: bool = False,
        upsert: bool = False,
        on_conflict: str | None = None,
        returning: bool = True,
    ) -> list[dict]:
        if not rows:
            return []
        bearer = self._db_token(token, admin)
        prefer = ["return=representation" if returning else "return=minimal"]
        if upsert:
            prefer.append("resolution=merge-duplicates")
        params = {"on_conflict": on_conflict} if on_conflict else None
        result = self._request(
            "POST",
            f"/api/database/records/{table}",
            token=bearer,
            params=params,
            json=rows,
            headers={"Prefer": ",".join(prefer)},
        )
        return result or []

    def update(
        self,
        table: str,
        params: dict,
        values: dict,
        *,
        token: str | None = None,
        admin: bool = False,
    ) -> list[dict]:
        bearer = self._db_token(token, admin)
        return self._request(
            "PATCH",
            f"/api/database/records/{table}",
            token=bearer,
            params=params,
            json=values,
            headers={"Prefer": "return=representation"},
        ) or []

    def delete(
        self,
        table: str,
        params: dict,
        *,
        token: str | None = None,
        admin: bool = False,
    ) -> list[dict]:
        if not params:
            raise ValueError("delete() requires a filter.")
        bearer = self._db_token(token, admin)
        return self._request(
            "DELETE",
            f"/api/database/records/{table}",
            token=bearer,
            params=params,
            headers={"Prefer": "return=representation"},
        ) or []

