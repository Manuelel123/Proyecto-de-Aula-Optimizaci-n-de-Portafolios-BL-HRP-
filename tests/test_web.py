"""HTTP tests shared by every page (rendering, CSRF)."""

import unittest

from support import WebTestCase


class WebInterfaceTests(WebTestCase):
    def test_all_primary_pages_render(self) -> None:
        for path, title in (
            ("/", "ATLAS"),
            ("/monitoring", "Monitoreo de activos"),
            ("/hrp", "Optimización HRP"),
            ("/black-litterman", "Black-Litterman"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(title.encode(), response.data)

    def test_post_requires_csrf_token(self) -> None:
        response = self.client.post("/hrp", data={"action": "optimize"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("sesión del formulario expiró".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
