"""Outgoing email (verification and password-reset codes) over SMTP.

Configured for Gmail by default: ``MAIL_USERNAME`` is the Gmail address and
``MAIL_PASSWORD`` an *app password* (Google account → Security → 2-Step
Verification → App passwords; the normal password does not work).
``MAIL_SMTP_HOST``/``MAIL_SMTP_PORT`` (default smtp.gmail.com:465, SSL) and
``MAIL_FROM`` (default the username) can be changed.

In tests the messages go to ``app.extensions["mail_outbox"]``. Without SMTP
settings the debug server prints the message to its console (development
only); otherwise sending fails with ``MailError``.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)

OUTBOX_KEY = "mail_outbox"


class MailError(Exception):
    """The message could not be sent."""


def configure(app) -> None:
    app.config.setdefault("MAIL_SMTP_HOST", os.environ.get("MAIL_SMTP_HOST", "smtp.gmail.com"))
    app.config.setdefault("MAIL_SMTP_PORT", int(os.environ.get("MAIL_SMTP_PORT", "465")))
    app.config.setdefault("MAIL_USERNAME", os.environ.get("MAIL_USERNAME", ""))
    app.config.setdefault("MAIL_PASSWORD", os.environ.get("MAIL_PASSWORD", ""))
    app.config.setdefault("MAIL_FROM", os.environ.get("MAIL_FROM", ""))
    if app.testing:
        app.extensions[OUTBOX_KEY] = []


def send_mail(to: str, subject: str, body: str) -> None:
    app = current_app
    if app.testing:
        app.extensions[OUTBOX_KEY].append({"to": to, "subject": subject, "body": body})
        return
    username, password = app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"]
    if not username or not password:
        if app.debug:
            # Development fallback only: the code is shown on the server console.
            logger.warning("MAIL_USERNAME/MAIL_PASSWORD not set; email to %s not sent:\n%s\n%s", to, subject, body)
            return
        raise MailError("El envío de correos no está configurado en el servidor.")
    message = EmailMessage()
    message["From"] = app.config["MAIL_FROM"] or username
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL(app.config["MAIL_SMTP_HOST"], app.config["MAIL_SMTP_PORT"], context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        logger.error("Sending email to %s failed: %s", to, type(error).__name__)
        raise MailError("No se pudo enviar el correo. Intenta de nuevo en unos minutos.") from error


def send_code(to: str, purpose: str, code: str) -> None:
    """Email with a verification or reset code (plain text, in Spanish)."""
    if purpose == "verify":
        subject = "Atlas · Código para verificar tu correo"
        intro = "Usa este código para verificar tu correo en Atlas:"
    else:
        subject = "Atlas · Código para restablecer tu contraseña"
        intro = "Usa este código para crear una contraseña nueva en Atlas:"
    send_mail(
        to,
        subject,
        f"{intro}\n\n    {code}\n\nVence en 15 minutos y solo sirve una vez.\n"
        "Si no lo pediste, ignora este mensaje: nadie puede entrar sin este código.\n\n"
        "Atlas · Información educativa. No es recomendación de inversión ni asesoría financiera.\n",
    )
