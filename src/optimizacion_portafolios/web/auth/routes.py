"""Sign-in, sign-up, email verification, password reset and OAuth routes.

Flows follow the project's InsForge Auth configuration (``public-config``):
email verification and password reset use 6-digit codes, and OAuth uses PKCE
with the code verifier kept in the session.
"""

import base64
import hashlib
import logging
import re
import secrets

from flask import flash, redirect, render_template, request, session, url_for

from optimizacion_portafolios.insforge import InsForgeError
from optimizacion_portafolios.web.auth import bp
from optimizacion_portafolios.web.auth.throttle import login_throttle
from optimizacion_portafolios.web.auth.session import (
    access_token,
    current_user,
    insforge_client,
    login_user,
    logout_user,
    safe_next,
)

logger = logging.getLogger(__name__)

_PENDING_VERIFICATION = "auth_pending_email"
_PENDING_RESET = "auth_reset_email"
_OAUTH_STATE = "auth_oauth"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CODE_PATTERN = re.compile(r"^\d{6}$")
OAUTH_LABELS = {
    "google": "Google",
    "github": "GitHub",
    "microsoft": "Microsoft",
    "discord": "Discord",
    "linkedin": "LinkedIn",
    "facebook": "Facebook",
    "apple": "Apple",
    "x": "X",
    "spotify": "Spotify",
}


@bp.before_request
def require_configuration():
    if insforge_client() is None:
        flash("Las cuentas de usuario no están configuradas en este servidor.", "warning")
        return redirect(url_for("main.index"))
    return None


def _signed_in() -> bool:
    try:
        return current_user() is not None and access_token() is not None
    except InsForgeError:
        return False


def _public_config() -> dict:
    try:
        return insforge_client().public_config()
    except InsForgeError as error:
        logger.warning("InsForge public config unavailable: %s", error)
        return {}


def _oauth_providers(config: dict) -> list[tuple[str, str]]:
    return [
        (provider, OAUTH_LABELS.get(provider, provider.title()))
        for provider in config.get("oAuthProviders", [])
    ]


def _email_field() -> str:
    email = request.form.get("email", "").strip().lower()
    if not _EMAIL_PATTERN.match(email):
        raise ValueError("Escribe un correo electrónico válido.")
    return email


def _password_errors(password: str, config: dict) -> list[str]:
    """Password policy of the project, checked before calling InsForge."""
    errors = []
    minimum = int(config.get("passwordMinLength") or 6)
    if len(password) < minimum:
        errors.append(f"al menos {minimum} caracteres")
    if config.get("requireNumber") and not re.search(r"\d", password):
        errors.append("un número")
    if config.get("requireLowercase") and not re.search(r"[a-z]", password):
        errors.append("una minúscula")
    if config.get("requireUppercase") and not re.search(r"[A-Z]", password):
        errors.append("una mayúscula")
    if config.get("requireSpecialChar") and not re.search(r"[^A-Za-z0-9]", password):
        errors.append("un carácter especial")
    return errors


def _new_password(config: dict) -> str:
    password = request.form.get("password", "")
    if password != request.form.get("password_confirm", ""):
        raise ValueError("Las contraseñas no coinciden.")
    errors = _password_errors(password, config)
    if errors:
        raise ValueError("La contraseña debe tener " + ", ".join(errors) + ".")
    return password


def _code_field(field: str = "code") -> str:
    code = re.sub(r"\s", "", request.form.get(field, ""))
    if not _CODE_PATTERN.match(code):
        raise ValueError("El código debe tener 6 dígitos.")
    return code


def _finish_sign_in(auth_response: dict, next_url: str | None):
    login_user(auth_response)
    user = current_user() or {}
    flash(f"Hola, {user.get('name') or user.get('email')}. Sesión iniciada.", "success")
    return redirect(safe_next(next_url))


def _start_verification(email: str, next_url: str | None, send: bool):
    session[_PENDING_VERIFICATION] = {"email": email, "next": next_url}
    if send:
        insforge_client().send_verification(email)
    return redirect(url_for("auth.verify"))


@bp.route("/ingresar", methods=["GET", "POST"])
def login():
    next_url = request.values.get("next")
    if _signed_in():
        return redirect(safe_next(next_url))
    config = _public_config()
    email = ""
    if request.method == "POST":
        throttle_keys = ()
        try:
            email = _email_field()
            password = request.form.get("password", "")
            if not password:
                raise ValueError("Escribe tu contraseña.")
            throttle_keys = (f"email:{email}", f"ip:{request.remote_addr}")
            wait = login_throttle.retry_after(*throttle_keys)
            if wait:
                raise ValueError(
                    "Demasiados intentos fallidos. Vuelve a intentar en "
                    f"{max(1, round(wait / 60))} min."
                )
            response = insforge_client().sign_in(email, password)
            login_throttle.reset(f"email:{email}")
            return _finish_sign_in(response, next_url)
        except ValueError as error:
            flash(str(error), "error")
        except InsForgeError as error:
            if error.status == 401 and throttle_keys:
                login_throttle.record_failure(*throttle_keys)
            # Valid credentials of an unverified account answer 403.
            if error.code == "EMAIL_NOT_VERIFIED" or error.status == 403:
                flash("Tu correo aún no está verificado. Te enviamos un código.", "info")
                try:
                    return _start_verification(email, next_url, send=True)
                except InsForgeError as send_error:
                    flash(send_error.user_message, "error")
            elif error.status == 401:
                flash("Correo o contraseña incorrectos.", "error")
            else:
                flash(error.user_message, "error")
    return render_template(
        "auth/login.html",
        email=email,
        next_url=next_url or "",
        oauth_providers=_oauth_providers(config),
    )


@bp.route("/registro", methods=["GET", "POST"])
def register():
    next_url = request.values.get("next")
    if _signed_in():
        return redirect(safe_next(next_url))
    config = _public_config()
    if config.get("disableSignup"):
        flash("El registro de cuentas nuevas está deshabilitado.", "warning")
        return redirect(url_for("auth.login"))
    values = {"name": "", "email": ""}
    if request.method == "POST":
        values["name"] = request.form.get("name", "").strip()[:80]
        try:
            values["email"] = email = _email_field()
            password = _new_password(config)
            response = insforge_client().sign_up(email, password, values["name"] or None)
            if response.get("requireEmailVerification") or not response.get("accessToken"):
                flash(f"Te enviamos un código de verificación a {email}.", "info")
                return _start_verification(email, next_url, send=False)
            return _finish_sign_in(response, next_url)
        except ValueError as error:
            flash(str(error), "error")
        except InsForgeError as error:
            flash(error.user_message, "error")
    return render_template(
        "auth/register.html",
        values=values,
        next_url=next_url or "",
        oauth_providers=_oauth_providers(config),
        password_rules=_password_errors("", config),
    )


@bp.route("/verificar", methods=["GET", "POST"])
def verify():
    pending = session.get(_PENDING_VERIFICATION)
    if not pending:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        try:
            response = insforge_client().verify_email(pending["email"], _code_field())
            session.pop(_PENDING_VERIFICATION, None)
            flash("Correo verificado.", "success")
            return _finish_sign_in(response, pending.get("next"))
        except ValueError as error:
            flash(str(error), "error")
        except InsForgeError as error:
            flash(
                "El código no es válido o expiró."
                if error.status in (400, 401, 403)
                else error.user_message,
                "error",
            )
    return render_template("auth/verify.html", email=pending["email"])


@bp.post("/verificar/reenviar")
def resend_verification():
    pending = session.get(_PENDING_VERIFICATION)
    if not pending:
        return redirect(url_for("auth.login"))
    try:
        insforge_client().send_verification(pending["email"])
        flash("Te enviamos un código nuevo.", "info")
    except InsForgeError as error:
        flash(error.user_message, "error")
    return redirect(url_for("auth.verify"))


@bp.route("/recuperar", methods=["GET", "POST"])
def forgot_password():
    email = ""
    if request.method == "POST":
        try:
            email = _email_field()
            insforge_client().send_password_reset(email)
            session[_PENDING_RESET] = email
            flash(
                f"Si {email} tiene una cuenta, recibirás un código para restablecer la contraseña.",
                "info",
            )
            return redirect(url_for("auth.reset_password"))
        except ValueError as error:
            flash(str(error), "error")
        except InsForgeError as error:
            flash(error.user_message, "error")
    return render_template("auth/forgot.html", email=email)


@bp.route("/restablecer", methods=["GET", "POST"])
def reset_password():
    email = session.get(_PENDING_RESET)
    if not email:
        return redirect(url_for("auth.forgot_password"))
    config = _public_config()
    if request.method == "POST":
        try:
            code = _code_field()
            password = _new_password(config)
            client = insforge_client()
            client.reset_password(client.exchange_reset_code(email, code), password)
            session.pop(_PENDING_RESET, None)
            flash("Contraseña actualizada. Inicia sesión con la nueva contraseña.", "success")
            return redirect(url_for("auth.login"))
        except ValueError as error:
            flash(str(error), "error")
        except InsForgeError as error:
            flash(
                "El código no es válido o expiró."
                if error.status in (400, 401, 403)
                else error.user_message,
                "error",
            )
    return render_template(
        "auth/reset.html", email=email, password_rules=_password_errors("", config)
    )


@bp.post("/salir")
def logout():
    state = session.get("auth") or {}
    if state.get("access"):
        try:
            insforge_client().sign_out(state["access"])
        except InsForgeError as error:
            logger.info("InsForge logout failed (%s); clearing the session anyway", error)
    logout_user()
    flash("Cerraste sesión.", "info")
    return redirect(url_for("main.index"))


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@bp.get("/oauth/<provider>")
def oauth_start(provider: str):
    if provider not in _public_config().get("oAuthProviders", []):
        flash("Ese proveedor de acceso no está habilitado.", "error")
        return redirect(url_for("auth.login"))
    verifier, challenge = _pkce_pair()
    session[_OAUTH_STATE] = {"verifier": verifier, "next": request.args.get("next")}
    try:
        auth_url = insforge_client().oauth_url(
            provider, url_for("auth.oauth_callback", _external=True), challenge
        )
    except InsForgeError as error:
        flash(error.user_message, "error")
        return redirect(url_for("auth.login"))
    return redirect(auth_url)


@bp.get("/oauth/callback")
def oauth_callback():
    state = session.pop(_OAUTH_STATE, None)
    code = request.args.get("insforge_code")
    if request.args.get("insforge_status") == "error" or not code or not state:
        flash(
            request.args.get("insforge_error") or "No se pudo completar el acceso con el proveedor.",
            "error",
        )
        return redirect(url_for("auth.login"))
    try:
        response = insforge_client().oauth_exchange(code, state["verifier"])
    except InsForgeError as error:
        flash(error.user_message, "error")
        return redirect(url_for("auth.login"))
    return _finish_sign_in(response, state.get("next"))
