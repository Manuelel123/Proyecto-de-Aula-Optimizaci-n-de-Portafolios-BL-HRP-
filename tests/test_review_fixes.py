"""Fixes from the final review: login CSRF, GESTOR_EMAILS sync, stale profile, notices."""

import unittest

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import Profile
from optimizacion_portafolios.storage.users import get_user
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.auth.permissions import role_label
from optimizacion_portafolios.web.auth.roles import canonical_role
from optimizacion_portafolios.web.common.notice import EDUCATIONAL_NOTICE, with_notice_html
from support import TEST_PASSWORD, add_test_profile, create_test_user, csrf_from, login_session


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "x", "GESTOR_EMAILS": "jefe@example.com"})
        self.client = self.app.test_client()

    def test_sign_in_post_without_a_session_token_is_rejected(self):
        create_test_user(self.app, "ana@example.com")
        response = self.client.post("/auth/ingresar", data={"email": "ana@example.com", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)
        token = csrf_from(self.client.get("/auth/ingresar"))
        ok = self.client.post(
            "/auth/ingresar", data={"email": "ana@example.com", "password": TEST_PASSWORD, "csrf_token": token}
        )
        self.assertEqual(ok.status_code, 302)

    def test_removing_a_gestor_email_applies_on_the_next_request(self):
        user_id = create_test_user(self.app, "jefe@example.com", role="gestor")
        login_session(self.client, user_id)
        self.assertEqual(self.client.get("/monitoring").status_code, 200)
        self.app.config["GESTOR_EMAILS"] = ""
        self.client.get("/")
        with self.app.app_context():
            self.assertNotEqual(get_user(user_id).role, "gestor")
        self.assertEqual(self.client.get("/monitoring").status_code, 302)

    def test_a_replaced_profile_is_reloaded_from_the_database(self):
        user_id = create_test_user(self.app, "ana@example.com")
        login_session(self.client, user_id)
        self.client.get("/")
        with self.client.session_transaction() as session:
            old_id = session["perfil"]["id"]
        add_test_profile(self.app, user_id)  # e.g. repeated on another device
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.assertNotEqual(session["perfil"]["id"], old_id)
        with self.app.app_context():
            vigente = db.session.query(Profile).filter_by(usuario_id=int(user_id), vigente=True).one()
            self.assertEqual(vigente.id, session["perfil"]["id"])

    def test_legacy_role_names_read_as_novato(self):
        self.assertEqual(canonical_role("explorador"), "novato")
        self.assertEqual(canonical_role("gestor"), "gestor")
        self.assertEqual(role_label("explorador"), "Novato")

    def test_downloaded_reports_carry_the_notice(self):
        stamped = with_notice_html(b"<html><body class='x'><h1>Informe</h1></body></html>")
        self.assertIn(EDUCATIONAL_NOTICE.encode("utf-8"), stamped)
        self.assertLess(stamped.index(EDUCATIONAL_NOTICE.encode("utf-8")), stamped.index(b"<h1>"))


if __name__ == "__main__":
    unittest.main()
