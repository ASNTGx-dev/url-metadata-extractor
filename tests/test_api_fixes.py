"""Regression tests for the 2026-11-12 API fixes (ChatGPT review "muse (3)").

Fix #1 — favicon fallback: when a page declares no icon tag, the API
must return favicon=null instead of guessing "/favicon.ico" (the guess
404s on real sites such as example.com).

Fix #4 — error granularity: target failures must not all collapse to a
bare 502. Distinct, actionable results:
  * target 404            -> 404 "page not found"
  * target 401 / 403 / 429 -> 403 "target blocked the request"
  * target 5xx            -> 502 "target server error"
  * target down/unreachable (connect failure) -> 502 "down or unreachable"
  * target timeout        -> 504 (unchanged behaviour)

Run:  cd <project dir>
      RAPIDAPI_PROXY_SECRET=<throwaway> \
        /home/hatch/workspace/automation/venv/bin/python -m unittest \
        discover -s tests -v

No network fetch ever leaves the test process: the HTTP layer is faked
(monkeypatched httpx.AsyncClient). Only _validate_url does real DNS
(example.com resolves through the VM egress proxy into 198.18.0.0/15,
which the SSRF check explicitly allows). The secret used here is a
throwaway local value only.
"""

import os
import sys
import unittest
import unittest.mock

os.environ.setdefault("RAPIDAPI_PROXY_SECRET", "regression-test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SECRET = "regression-test-secret"
HEADERS = {"X-RapidAPI-Proxy-Secret": SECRET}

HTML_NO_ICON = b"""<!doctype html><html lang="en"><head>
<title>Example Domain</title></head>
<body><h1>Example Domain</h1><p>Some text here.</p></body></html>"""

HTML_WITH_ICON = b"""<!doctype html><html lang="en"><head>
<title>With Icon</title>
<link rel="icon" href="/static/favicon.ico">
</head><body><p>Text.</p></body></html>"""

HTML_SHORTCUT_ICON = b"""<!doctype html><html><head>
<title>Shortcut</title>
<link rel="shortcut icon" href="https://cdn.example.com/fav.png">
</head><body><p>Text.</p></body></html>"""


class FakeResponse:
    """Minimal stand-in for httpx.Response used inside client.stream()."""

    def __init__(self, status_code, body=b"", content_type="text/html; charset=utf-8",
                 url="https://example.com"):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": content_type}
        self.url = httpx.URL(url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_bytes(self, chunk_size=65536):
        yield self._body


def make_fake_client(behavior):
    """behavior: callable returning a FakeResponse, or raising an httpx error."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url):
            return behavior()

    return FakeClient


class FaviconTests(unittest.TestCase):
    """Fix #1 — no guessed /favicon.ico."""

    def test_no_icon_tag_returns_null(self):
        meta = app_module._extract_metadata(HTML_NO_ICON, "https://example.com")
        self.assertIsNone(meta["favicon"])

    def test_icon_tag_is_resolved(self):
        meta = app_module._extract_metadata(HTML_WITH_ICON, "https://example.com")
        self.assertEqual(meta["favicon"], "https://example.com/static/favicon.ico")

    def test_shortcut_icon_tag_is_resolved(self):
        meta = app_module._extract_metadata(
            HTML_SHORTCUT_ICON, "https://example.com"
        )
        self.assertEqual(meta["favicon"], "https://cdn.example.com/fav.png")

    def test_endpoint_returns_null_favicon_for_page_without_icon(self):
        with unittest.mock.patch.object(
            httpx, "AsyncClient",
            make_fake_client(lambda: FakeResponse(200, HTML_NO_ICON)),
        ):
            client = TestClient(app_module.app)
            resp = client.post(
                "/v1/extract",
                json={"url": "https://example.com"},
                headers={**HEADERS, "X-RapidAPI-User": "favicon-null-test"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["favicon"])
        self.assertEqual(resp.json()["title"], "Example Domain")


class ErrorGranularityTests(unittest.TestCase):
    """Fix #4 — 404 vs blocked vs down are distinct results."""

    def setUp(self):
        app_module._request_log.clear()

    def _extract_with(self, behavior, user):
        with unittest.mock.patch.object(
            httpx, "AsyncClient", make_fake_client(behavior)
        ):
            client = TestClient(app_module.app)
            return client.post(
                "/v1/extract",
                json={"url": "https://example.com/some-page"},
                headers={**HEADERS, "X-RapidAPI-User": user},
            )

    def test_target_404_returns_404_not_502(self):
        resp = self._extract_with(lambda: FakeResponse(404), "err-404")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("404", resp.json()["detail"])
        self.assertIn("not found", resp.json()["detail"].lower())

    def test_target_403_returns_403_blocked(self):
        resp = self._extract_with(lambda: FakeResponse(403), "err-403")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("blocked", resp.json()["detail"].lower())

    def test_target_401_returns_403_blocked(self):
        resp = self._extract_with(lambda: FakeResponse(401), "err-401")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("blocked", resp.json()["detail"].lower())

    def test_target_429_returns_403_blocked(self):
        resp = self._extract_with(lambda: FakeResponse(429), "err-429")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("blocked", resp.json()["detail"].lower())

    def test_target_500_returns_502_server_error(self):
        resp = self._extract_with(lambda: FakeResponse(500), "err-500")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("server error", resp.json()["detail"].lower())

    def test_target_down_returns_502_unreachable_distinct_from_blocked(self):
        def raise_connect():
            raise httpx.ConnectError("connection refused", request=httpx.Request("GET", "https://example.com"))

        resp = self._extract_with(raise_connect, "err-down")
        self.assertEqual(resp.status_code, 502)
        detail = resp.json()["detail"].lower()
        self.assertIn("unreachable", detail)
        self.assertNotIn("blocked", detail)

    def test_target_timeout_still_504(self):
        def raise_timeout():
            raise httpx.ConnectTimeout("timed out", request=httpx.Request("GET", "https://example.com"))

        resp = self._extract_with(raise_timeout, "err-timeout")
        self.assertEqual(resp.status_code, 504)

    def test_success_still_200(self):
        resp = self._extract_with(
            lambda: FakeResponse(200, HTML_WITH_ICON), "err-ok"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["favicon"], "https://example.com/static/favicon.ico"
        )


if __name__ == "__main__":
    unittest.main()
