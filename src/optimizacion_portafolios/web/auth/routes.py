"""Local sign-up, sign-in and sign-out (email + password, argon2 hashes)."""

import logging
import re

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import DEFAULT_ROLE
from optimizacion_portafolios.storage.users import (
    create_user,
    get_user_by_email,
    normalize_email,
)
from optimizacion_portafolios.web.auth import bp
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


def _email_field() -> str:
    email = normalize_email(request.form.get("email"))
    if len(email) > _EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(email):
        raise ValueError("Escribe un correo electrónico válido.")
    return email


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
            flash("Cuenta creada.", "success")
            return _finish_sign_in(user, next_url)
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
