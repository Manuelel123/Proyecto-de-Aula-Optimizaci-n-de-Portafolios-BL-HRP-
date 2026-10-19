"""In-memory stand-in for ``InsForgeClient`` (no network) used by the tests."""

import base64
import json
import re
import time
import uuid

from optimizacion_portafolios.insforge import InsForgeError, InsForgeSettings


def make_jwt(subject: str = "user-1", lifetime: float = 3600) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return ".".join(
        [part({"alg": "HS256"}), part({"sub": subject, "exp": time.time() + lifetime}), "sig"]
    )


def _matches(row: dict, params: dict) -> bool:
    for key, condition in params.items():
        if key in {"select", "order", "limit", "offset", "on_conflict"}:
            continue
        if key == "and":
            for clause in condition.strip("()").split(","):
                column, operator, value = clause.split(".", 2)
                cell = str(row.get(column))
                if operator == "gte" and not cell >= value:
                    return False
                if operator == "lte" and not cell <= value:
                    return False
            continue
        operator, value = condition.split(".", 1)
        if operator == "eq" and str(row.get(key)) != value:
            return False
        if operator == "in":
            values = re.findall(r'"((?:[^"\\]|\\.)*)"', value)
            if str(row.get(key)) not in values:
                return False
    return True


class FakeInsForge:
    """Auth and database methods of ``InsForgeClient`` over dict tables.

    Rows written with a user token get that user's id, and reads/deletes with
    a user token only see that user's rows (mimicking the owner-only RLS).
    """

    def __init__(self, admin_key: str | None = "admin-key"):
        self.settings = InsForgeSettings("https://fake.insforge.app", "anon", admin_key)
        self.tables: dict[str, list[dict]] = {}
        self.users: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.config = {
            "oAuthProviders": ["google", "github"],
            "requireEmailVerification": True,
            "passwordMinLength": 6,
            "disableSignup": False,
        }
        self.tokens: dict[str, str] = {}  # access token -> user id
        self.refresh_tokens: dict[str, str] = {}
        self.verification_codes: dict[str, str] = {}

    @property
    def has_admin_key(self) -> bool:
        return bool(self.settings.api_key)

    # ---------------------------------------------------------------- auth
    def public_config(self):
        return self.config

    def _session(self, user: dict, lifetime: float = 3600) -> dict:
        access = make_jwt(user["id"], lifetime)
        refresh = f"refresh-{uuid.uuid4()}"
        self.tokens[access] = user["id"]
        self.refresh_tokens[refresh] = user["id"]
        return {
            "user": {"id": user["id"], "email": user["email"], "profile": {"name": user["name"]}},
            "accessToken": access,
            "refreshToken": refresh,
        }

    def add_user(self, email: str, password: str, name: str = "Ana", verified: bool = True) -> dict:
        user = {"id": str(uuid.uuid4()), "email": email, "password": password, "name": name, "verified": verified}
        self.users[email] = user
        return user

    def sign_up(self, email, password, name=None):
        self.calls.append(("sign_up", email))
        if email in self.users:
            raise InsForgeError("User already exists", 409, "AUTH_EMAIL_EXISTS")
        user = self.add_user(email, password, name or "", verified=False)
        self.verification_codes[email] = "123456"
        return {"user": {"id": user["id"], "email": email}, "accessToken": None, "requireEmailVerification": True}

    def sign_in(self, email, password):
        self.calls.append(("sign_in", email))
        user = self.users.get(email)
        if not user or user["password"] != password:
            raise InsForgeError("Invalid credentials", 401, "AUTH_UNAUTHORIZED")
        if not user["verified"]:
            raise InsForgeError("Email not verified", 403, "FORBIDDEN")
        return self._session(user)

    def send_verification(self, email):
        self.calls.append(("send_verification", email))
        self.verification_codes[email] = "123456"

    def verify_email(self, email, otp):
        if self.verification_codes.get(email) != otp:
            raise InsForgeError("Invalid code", 400, "INVALID_INPUT")
        self.users[email]["verified"] = True
        return self._session(self.users[email])

    def refresh(self, refresh_token):
        self.calls.append(("refresh",))
        user_id = self.refresh_tokens.pop(refresh_token, None)
        if user_id is None:
            raise InsForgeError("expired", 401, "INVALID_TOKEN")
        user = next(user for user in self.users.values() if user["id"] == user_id)
        return self._session(user)

    def sign_out(self, access_token):
        self.calls.append(("sign_out",))

    def oauth_url(self, provider, redirect_uri, code_challenge):
        self.calls.append(("oauth_url", provider, redirect_uri, code_challenge))
        return f"https://accounts.example/{provider}?challenge={code_challenge}"

    def oauth_exchange(self, code, code_verifier):
        self.calls.append(("oauth_exchange", code, code_verifier))
        user = self.users.get("oauth@example.com") or self.add_user("oauth@example.com", "", "OAuth")
        return self._session(user)

    def send_password_reset(self, email):
        self.calls.append(("send_password_reset", email))

    def exchange_reset_code(self, email, code):
        if code != "654321":
            raise InsForgeError("bad code", 400)
        return f"reset-{email}"

    def reset_password(self, reset_token, new_password):
        email = reset_token.removeprefix("reset-")
        self.users[email]["password"] = new_password

    # ------------------------------------------------------------ database
    def _owner(self, token, admin):
        if admin:
            if not self.settings.api_key:
                raise InsForgeError("no admin key")
            return None
        if token not in self.tokens:
            raise InsForgeError("unauthorized", 401)
        return self.tokens[token]

    def _visible(self, table, owner):
        rows = self.tables.setdefault(table, [])
        return [row for row in rows if owner is None or row.get("user_id") == owner]

    def select(self, table, params=None, *, token=None, admin=False, limit=None):
        owner = self._owner(token, admin)
        self.calls.append(("select", table))
        rows = [dict(row) for row in self._visible(table, owner) if _matches(row, params or {})]
        return rows[:limit] if limit is not None else rows

    def insert(self, table, rows, *, token=None, admin=False, upsert=False, on_conflict=None, returning=True):
        owner = self._owner(token, admin)
        self.calls.append(("insert", table, len(rows)))
        stored = self.tables.setdefault(table, [])
        created = []
        keys = on_conflict.split(",") if on_conflict else None
        for row in rows:
            row = dict(row)
            if owner is not None:
                row["user_id"] = owner
            if "id" not in row and table in {"saved_portfolios", "backtest_runs"}:
                row["id"] = str(uuid.uuid4())
            row.setdefault("created_at", "2026-10-14T12:00:00+00:00")
            if upsert and keys:
                existing = next(
                    (old for old in stored if all(old.get(key) == row.get(key) for key in keys)),
                    None,
                )
                if existing is not None:
                    existing.update(row)
                    created.append(dict(existing))
                    continue
            stored.append(row)
            created.append(dict(row))
        return created if returning else []

    def update(self, table, params, values, *, token=None, admin=False):
        owner = self._owner(token, admin)
        changed = []
        for row in self._visible(table, owner):
            if _matches(row, params):
                row.update(values)
                changed.append(dict(row))
        return changed

    def delete(self, table, params, *, token=None, admin=False):
        owner = self._owner(token, admin)
        doomed = [row for row in self._visible(table, owner) if _matches(row, params)]
        self.tables[table] = [row for row in self.tables.get(table, []) if row not in doomed]
        if table == "saved_portfolios":
            ids = {row["id"] for row in doomed}
            self.tables["portfolio_weights"] = [
                row for row in self.tables.get("portfolio_weights", []) if row["portfolio_id"] not in ids
            ]
        if table == "backtest_runs":
            ids = {row["id"] for row in doomed}
            self.tables["backtest_results"] = [
                row for row in self.tables.get("backtest_results", []) if row["run_id"] not in ids
            ]
        return doomed
