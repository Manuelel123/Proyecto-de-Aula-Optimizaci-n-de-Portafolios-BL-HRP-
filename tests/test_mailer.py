"""Mail backends: Brevo's HTTPS API (hosts without SMTP) and the SMTP fallback."""

import unittest
from unittest.mock import MagicMock, patch

import requests
from flask import Flask

from optimizacion_portafolios.web.auth import mailer
from optimizacion_portafolios.web.auth.mailer import MailError


def make_app(**config):
    app = Flask(__name__)
    # Explicit values so the machine's environment never leaks in.
    app.config.update({"BREVO_API_KEY": "", "MAIL_FROM": "", "MAIL_USERNAME": "", "MAIL_PASSWORD": "", **config})
    mailer.configure(app)
    return app


def brevo_response(status, text=""):
    return MagicMock(status_code=status, text=text)


@patch("optimizacion_portafolios.web.auth.mailer.smtplib.SMTP_SSL")
@patch("optimizacion_portafolios.web.auth.mailer.requests.post")
class BrevoTests(unittest.TestCase):
    def test_sends_through_the_api_with_the_named_sender(self, post, smtp):
        post.return_value = brevo_response(201, '{"messageId": "<1@brevo>"}')
        app = make_app(BREVO_API_KEY="xkeysib-1", MAIL_FROM="Atlas <atlas@gmail.com>")
        with app.app_context():
            mailer.send_mail("ana@example.com", "Asunto", "Cuerpo")
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(url, mailer.BREVO_URL)
        self.assertEqual(kwargs["headers"]["api-key"], "xkeysib-1")
        self.assertEqual(
            kwargs["json"],
            {
                "sender": {"email": "atlas@gmail.com", "name": "Atlas"},
                "to": [{"email": "ana@example.com"}],
                "subject": "Asunto",
                "textContent": "Cuerpo",
            },
        )
        smtp.assert_not_called()

    def test_bare_address_sender_has_no_name(self, post, _smtp):
        post.return_value = brevo_response(201)
        app = make_app(BREVO_API_KEY="k", MAIL_FROM="atlas@gmail.com")
        with app.app_context():
            mailer.send_mail("ana@example.com", "S", "B")
        self.assertEqual(post.call_args.kwargs["json"]["sender"], {"email": "atlas@gmail.com"})

    def test_rejection_and_network_errors_raise_mail_error(self, post, _smtp):
        app = make_app(BREVO_API_KEY="k", MAIL_FROM="atlas@gmail.com")
        for outcome in (
            brevo_response(400, '{"code": "invalid_parameter", "message": "sender not valid"}'),
            requests.ConnectionError("down"),
        ):
            with self.subTest(outcome=outcome):
                post.reset_mock(return_value=True, side_effect=True)
                if isinstance(outcome, Exception):
                    post.side_effect = outcome
                else:
                    post.return_value = outcome
                with app.app_context(), self.assertRaises(MailError):
                    mailer.send_mail("ana@example.com", "S", "B")

    def test_missing_sender_is_a_configuration_error(self, post, _smtp):
        app = make_app(BREVO_API_KEY="k")
        with app.app_context(), self.assertRaises(MailError):
            mailer.send_mail("ana@example.com", "S", "B")
        post.assert_not_called()

    def test_pasted_whitespace_is_trimmed_from_the_environment(self, post, _smtp):
        post.return_value = brevo_response(201)
        app = Flask(__name__)
        environ = {"BREVO_API_KEY": " xkeysib-1\n", "MAIL_FROM": "Atlas <atlas@gmail.com>\n"}
        with patch.dict("os.environ", environ):
            mailer.configure(app)
        with app.app_context():
            mailer.send_mail("ana@example.com", "S", "B")
        self.assertEqual(post.call_args.kwargs["headers"]["api-key"], "xkeysib-1")
        self.assertEqual(post.call_args.kwargs["json"]["sender"]["email"], "atlas@gmail.com")

    def test_without_api_key_smtp_is_used(self, post, smtp):
        app = make_app(MAIL_USERNAME="atlas@gmail.com", MAIL_PASSWORD="app-pass")
        with app.app_context():
            mailer.send_mail("ana@example.com", "S", "B")
        post.assert_not_called()
        smtp.return_value.__enter__.return_value.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
