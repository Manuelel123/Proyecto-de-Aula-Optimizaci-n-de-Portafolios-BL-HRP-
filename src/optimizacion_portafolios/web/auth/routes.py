"""Local sign-up, sign-in, email verification, password reset and sign-out.

Passwords are argon2 hashes. An account must prove its email with a 6-digit
code before its first sign-in; the same kind of code resets a forgotten
password. Messages never reveal whether an email has an account.
"""

import logging
import re

from datetime import datetime, timezone

from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.email_codes import check_code, issue_code, seconds_until_resend
from optimizacion_portafolios.storage.models import DEFAULT_ROLE
from optimizacion_portafolios.storage.users import (
    create_user,
    get_user,
    get_user_by_email,
    normalize_email,
)
from optimizacion_portafolios.web.auth import bp
from optimizacion_portafolios.web.auth.mailer import MailError, send_code
from optimizacion_portafolios.web.auth.passwords import (
    hash_password,
    needs_rehash,
    password_errors,
    password_rules,
    verify_password,
)
from optimizacion_portafolios.web.auth.session import (
    after_login_response,
    login_user,
    logout_user,
    safe_next,
)
from optimizacion_portafolios.web.auth.throttle import login_throttle

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_MAX_LENGTH = 254
_NAME_MAX_LENGTH = 80
_BAD_CREDENTIALS = "Correo o contraseña incorrectos."
_BAD_CODE = "El código no es correcto o ya venció. Revisa el último correo o pide uno nuevo."
_PENDING_VERIFY = "auth_verify"
_PENDING_RESET = "auth_reset"
_CODE_PATTERN = re.compile(r"^\d{6}$")


def _email_field() -> str:
    email = normalize_email(request.form.get("email"))
    if len(email) > _EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(email):
        raise ValueError("Escribe un correo electrónico válido.")
    return email


def _code_field() -> str:
    code = re.sub(r"\s", "", request.form.get("code", ""))
    if not _CODE_PATTERN.match(code):
        raise ValueError("El código tiene 6 dígitos.")
    return code


def _send(user, purpose: str) -> bool:
    """Issue and email a code unless one was sent moments ago; True if sent."""
    if seconds_until_resend(user.id, purpose):
        return False
    code = issue_code(user.id, purpose, current_app.secret_key)
    try:
        send_code(user.email, purpose, code)
    except MailError as error:
        flash(str(error), "error")
        return False
    return True


def _start_verification(user, next_url: str | None):
    session[_PENDING_VERIFY] = {"user_id": user.id, "next": next_url}
    if _send(user, "verify"):
        flash(f"Te enviamos un código de verificación a {user.email}.", "info")
    return redirect(url_for("auth.verify"))


def _finish_sign_in(user, next_url: str | None):
    login_user(user)
    flash(f"Hola, {user.name}. Sesión iniciada.", "success")
    return after_login_response(user) or redirect(safe_next(next_url))


@bp.route("/ingresar", methods=["GET", "POST"])
def login():
    next_url = request.values.get("next")
    if current_user.is_authenticated:
        return redirect(safe_next(next_url))
    email = ""
    if request.method == "POST":
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
            user = get_user_by_email(email)
            if not verify_password(user.password_hash if user else None, password):
                login_throttle.record_failure(*throttle_keys)
                raise ValueError(_BAD_CREDENTIALS)
            login_throttle.reset(f"email:{email}")
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)
                db.session.commit()
            if not user.email_verified:
                return _start_verification(user, next_url)
            return _finish_sign_in(user, next_url)
        except ValueError as error:
            flash(str(error), "error")
    return render_template("auth/login.html", email=email, next_url=next_url or "")


@bp.route("/registro", methods=["GET", "POST"])
def register():
    next_url = request.values.get("next")
    if current_user.is_authenticated:
        return redirect(safe_next(next_url))
    values = {"name": "", "email": ""}
    if request.method == "POST":
        values["name"] = request.form.get("name", "").strip()[:_NAME_MAX_LENGTH]
        try:
            values["email"] = email = _email_field()
            password = request.form.get("password", "")
            if password != request.form.get("password_confirm", ""):
                raise ValueError("Las contraseñas no coinciden.")
            errors = password_errors(password, email)
            if errors:
                raise ValueError(" ".join(errors))
            # The role never comes from the form; gestor is granted only by CLI.
            role, confirmed = DEFAULT_ROLE, False
            try:
                user = create_user(
                    email,
                    hash_password(password),
                    display_name=values["name"] or None,
                    role=role,
                    role_confirmed=confirmed,
                )
            except IntegrityError as error:  # concurrent sign-up with the same email
                db.session.rollback()
                raise ValueError("Ya existe una cuenta con ese correo.") from error
            logger.info("New local account %s (role %s)", user.id, user.role)
            flash("Cuenta creada. Verifica tu correo para entrar.", "success")
            return _start_verification(user, next_url)
        except ValueError as error:
            flash(str(error), "error")
    return render_template(
        "auth/register.html",
        values=values,
        next_url=next_url or "",
        password_rules=password_rules(),
    )


@bp.post("/salir")
def logout():
    logout_user()
    flash("Cerraste sesión.", "info")
    return redirect(url_for("main.index"))


@bp.route("/verificar", methods=["GET", "POST"])
def verify():
    pending = session.get(_PENDING_VERIFY) or {}
    user = get_user(pending.get("user_id")) if pending else None
    if user is None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        try:
            if not check_code(user.id, "verify", _code_field(), current_app.secret_key):
                raise ValueError(_BAD_CODE)
        except ValueError as error:
            flash(str(error), "error")
        else:
            user.email_verified_at = datetime.now(timezone.utc)
            db.session.commit()
            session.pop(_PENDING_VERIFY, None)
            flash("Correo verificado.", "success")
            return _finish_sign_in(user, pending.get("next"))
    return render_template("auth/verify.html", email=user.email)


@bp.post("/verificar/reenviar")
def resend_verification():
    pending = session.get(_PENDING_VERIFY) or {}
    user = get_user(pending.get("user_id")) if pending else None
    if user is None:
        return redirect(url_for("auth.login"))
    wait = seconds_until_resend(user.id, "verify")
    if wait:
        flash(f"Espera {wait} s para pedir otro código.", "info")
    elif _send(user, "verify"):
        flash("Te enviamos un código nuevo.", "info")
    return redirect(url_for("auth.verify"))


@bp.route("/recuperar", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    email = ""
    if request.method == "POST":
        try:
            email = _email_field()
        except ValueError as error:
            flash(str(error), "error")
        else:
            user = get_user_by_email(email)
            if user is not None:
                _send(user, "reset")
            session[_PENDING_RESET] = email
            # Same answer whether or not the email has an account.
            flash(f"Si {email} tiene una cuenta, recibirás un código para crear una contraseña nueva.", "info")
            return redirect(url_for("auth.reset_password"))
    return render_template("auth/forgot.html", email=email)


@bp.route("/restablecer", methods=["GET", "POST"])
def reset_password():
    email = session.get(_PENDING_RESET)
    if not email:
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        try:
            code = _code_field()
            password = request.form.get("password", "")
            if password != request.form.get("password_confirm", ""):
                raise ValueError("Las contraseñas no coinciden.")
            errors = password_errors(password, email)
            if errors:
                raise ValueError(" ".join(errors))
            user = get_user_by_email(email)
            if user is None or not check_code(user.id, "reset", code, current_app.secret_key):
                raise ValueError(_BAD_CODE)
        except ValueError as error:
            flash(str(error), "error")
        else:
            user.password_hash = hash_password(password)
            if not user.email_verified:  # the code proved the email too
                user.email_verified_at = datetime.now(timezone.utc)
            db.session.commit()
            session.pop(_PENDING_RESET, None)
            login_throttle.reset(f"email:{email}")
            logger.info("Password reset for user %s", user.id)
            flash("Contraseña actualizada. Inicia sesión con la nueva.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", email=email, password_rules=password_rules())
