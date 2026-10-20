"""Email verification, password reset and the one-time codes behind them."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.email_codes import MAX_ATTEMPTS, check_code, issue_code, seconds_until_resend
from optimizacion_portafolios.storage.models import EmailCode
from optimizacion_portafolios.web.auth.passwords import verify_password
from support import TEST_PASSWORD
from test_web_accounts import AccountTestCase


def path_of(response) -> str:
    return urlsplit(response.location).path


class CodeTests(AccountTestCase):
    def test_codes_are_single_use_and_stored_hashed(self):
        user_id = int(self.create_user("ana@example.com"))
        with self.app.app_context():
            code = issue_code(user_id, "verify", "secret")
            stored = db.session.query(EmailCode).one()
            self.assertNotIn(code, stored.code_hash)
            self.assertFalse(check_code(user_id, "verify", "000000" if code != "000000" else "111111", "secret"))
            self.assertTrue(check_code(user_id, "verify", code, "secret"))
            self.assertFalse(check_code(user_id, "verify", code, "secret"))  # used once

    def test_codes_expire_and_burn_after_too_many_attempts(self):
        user_id = int(self.create_user("ana@example.com"))
        with self.app.app_context():
            code = issue_code(user_id, "reset", "secret")
            for _ in range(MAX_ATTEMPTS):
                check_code(user_id, "reset", "999999" if code != "999999" else "888888", "secret")
            self.assertFalse(check_code(user_id, "reset", code, "secret"))
            code = issue_code(user_id, "reset", "secret")
            stored = db.session.query(EmailCode).one()
            stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.session.commit()
            self.assertFalse(check_code(user_id, "reset", code, "secret"))

    def test_resend_cooldown(self):
        user_id = int(self.create_user("ana@example.com"))
        with self.app.app_context():
            self.assertEqual(seconds_until_resend(user_id, "verify"), 0)
            issue_code(user_id, "verify", "secret")
            self.assertGreater(seconds_until_resend(user_id, "verify"), 0)


class VerificationFlowTests(AccountTestCase):
    def test_unverified_account_gets_a_code_instead_of_a_session(self):
        self.create_user("ana@example.com", verified=False)
        response = self.post("/auth/ingresar", email="ana@example.com", password=TEST_PASSWORD)
        self.assertEqual(path_of(response), "/auth/verificar")
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)
        self.assertIn("Código para verificar", self.app.extensions["mail_outbox"][-1]["subject"])
        wrong = self.post("/auth/verificar", code="12345")
        self.assertIn("El código tiene 6 dígitos", wrong.data.decode())
        response = self.post("/auth/verificar", code=self.last_code("ana@example.com"))
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("_user_id", session)
        self.assertTrue(self.user("ana@example.com").email_verified)

    def test_wrong_code_does_not_sign_in(self):
        self.register(email="nueva@example.com")
        code = self.last_code("nueva@example.com")
        response = self.post("/auth/verificar", code="000000" if code != "000000" else "111111")
        self.assertIn("El código no es correcto", response.data.decode())
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)

    def test_verification_page_needs_a_pending_account(self):
        self.assertEqual(path_of(self.client.get("/auth/verificar")), "/auth/ingresar")


class PasswordResetTests(AccountTestCase):
    def test_forgot_and_reset_with_the_emailed_code(self):
        self.create_user("ana@example.com")
        response = self.post("/auth/recuperar", email="ANA@example.com")
        self.assertEqual(path_of(response), "/auth/restablecer")
        code = self.last_code("ana@example.com")
        mismatch = self.post("/auth/restablecer", code=code, password="nueva2026", password_confirm="otra2026")
        self.assertIn("no coinciden", mismatch.data.decode())
        response = self.post("/auth/restablecer", code=code, password="nueva2026", password_confirm="nueva2026")
        self.assertEqual(path_of(response), "/auth/ingresar")
        with self.app.app_context():
            from optimizacion_portafolios.storage.users import get_user_by_email

            self.assertTrue(verify_password(get_user_by_email("ana@example.com").password_hash, "nueva2026"))
        self.refresh_csrf()
        ok = self.post("/auth/ingresar", email="ana@example.com", password="nueva2026")
        self.assertEqual(ok.status_code, 302)
        self.assertNotEqual(path_of(ok), "/auth/verificar")

    def test_unknown_email_gets_the_same_answer_and_no_mail(self):
        response = self.post("/auth/recuperar", email="nadie@example.com")
        self.assertEqual(path_of(response), "/auth/restablecer")
        self.assertEqual(self.app.extensions["mail_outbox"], [])
        follow = self.client.get("/auth/restablecer").data.decode()
        self.assertIn("nadie@example.com", follow)
        bad = self.post("/auth/restablecer", code="123456", password="nueva2026", password_confirm="nueva2026")
        self.assertIn("El código no es correcto", bad.data.decode())

    def test_reset_also_verifies_the_email(self):
        self.create_user("ana@example.com", verified=False)
        self.post("/auth/recuperar", email="ana@example.com")
        code = self.last_code("ana@example.com")
        self.post("/auth/restablecer", code=code, password="nueva2026", password_confirm="nueva2026")
        self.assertTrue(self.user("ana@example.com").email_verified)


class GestorNeedsVerifiedEmailTests(AccountTestCase):
    def test_make_gestor_refuses_unverified_accounts(self):
        self.create_user("jefa@example.com", verified=False)
        result = self.app.test_cli_runner().invoke(args=["users", "make-gestor", "jefa@example.com"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("aún no verificó su correo", result.output)
        self.assertEqual(self.user("jefa@example.com").role, "novato")
