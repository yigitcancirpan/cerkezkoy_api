import unittest

from services.static_page_guard import (
    LINE_SCOPED_STATIC_PAGES,
    get_line_id,
    line_context_redirect_url,
)


class StaticPageGuardTest(unittest.TestCase):
    def test_every_scoped_page_requires_line(self):
        for path in LINE_SCOPED_STATIC_PAGES:
            with self.subTest(path=path):
                redirect = line_context_redirect_url(path, {})
                self.assertIsNotNone(redirect)
                self.assertTrue(redirect.startswith("/static/home.html?"))

    def test_home_and_assets_remain_public_without_line(self):
        self.assertIsNone(line_context_redirect_url("/static/home.html", {}))
        self.assertIsNone(line_context_redirect_url("/static/app.css", {}))

    def test_line_and_line_id_are_both_supported(self):
        path = "/static/dashboard.html"
        self.assertIsNone(line_context_redirect_url(path, {"line": "2"}))
        self.assertIsNone(line_context_redirect_url(path, {"line_id": "2"}))
        self.assertEqual(get_line_id({"line": "2", "line_id": "2"}), 2)

    def test_invalid_or_conflicting_line_is_rejected(self):
        path = "/static/dashboard.html"
        invalid_queries = (
            {"line": ""},
            {"line": "abc"},
            {"line": "0"},
            {"line": "-1"},
            {"line": "1", "line_id": "2"},
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                self.assertIsNotNone(line_context_redirect_url(path, query))


if __name__ == "__main__":
    unittest.main()
