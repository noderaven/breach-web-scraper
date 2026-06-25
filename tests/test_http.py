from __future__ import annotations

import unittest
from http.client import BadStatusLine
from unittest import mock
from urllib.error import HTTPError, URLError

from breach_scraper.http import fetch_url


class _FakeHeaders:
    def __init__(self, charset: str = "utf-8") -> None:
        self._charset = charset

    def get_content_charset(self) -> str:
        return self._charset


class _FakeResponse:
    def __init__(self, body: bytes, charset: str = "utf-8") -> None:
        self._body = body
        self.headers = _FakeHeaders(charset)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class TestFetchUrl(unittest.TestCase):
    @mock.patch("breach_scraper.http.time.sleep", return_value=None)
    @mock.patch("breach_scraper.http.urlopen")
    def test_retries_then_succeeds(self, mock_urlopen: mock.Mock, _sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = [URLError("boom"), _FakeResponse(b"<html>ok</html>")]
        result = fetch_url(["https://example.test"], retries=3, backoff=0)
        self.assertEqual(result, "<html>ok</html>")
        self.assertEqual(mock_urlopen.call_count, 2)

    @mock.patch("breach_scraper.http.time.sleep", return_value=None)
    @mock.patch("breach_scraper.http.urlopen")
    def test_retries_on_protocol_error(self, mock_urlopen: mock.Mock, _sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = [BadStatusLine("x"), _FakeResponse(b"ok")]
        self.assertEqual(fetch_url(["https://example.test"], retries=2, backoff=0), "ok")

    @mock.patch("breach_scraper.http.time.sleep", return_value=None)
    @mock.patch("breach_scraper.http.urlopen")
    def test_exhausted_raises(self, mock_urlopen: mock.Mock, _sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = URLError("down")
        with self.assertRaises(RuntimeError):
            fetch_url(["https://example.test"], retries=2, backoff=0)

    @mock.patch("breach_scraper.http.time.sleep", return_value=None)
    @mock.patch("breach_scraper.http.urlopen")
    def test_http_403_actionable(self, mock_urlopen: mock.Mock, _sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = HTTPError("u", 403, "Forbidden", None, None)
        with self.assertRaises(RuntimeError) as ctx:
            fetch_url(["https://example.test"], retries=3, backoff=0)
        message = str(ctx.exception)
        self.assertIn("403", message)
        self.assertIn("--input-html", message)

    @mock.patch("breach_scraper.http.urlopen")
    def test_unknown_charset_falls_back_to_utf8(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value = _FakeResponse(b"caf\xc3\xa9", charset="bogus-charset")
        self.assertIn("caf", fetch_url(["https://example.test"], retries=1, backoff=0))

    @mock.patch("breach_scraper.http.time.sleep")
    @mock.patch("breach_scraper.http.urlopen")
    def test_backoff_is_capped(self, mock_urlopen: mock.Mock, mock_sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = URLError("down")
        with self.assertRaises(RuntimeError):
            fetch_url(["https://example.test"], retries=20, backoff=1.0)
        self.assertLessEqual(max(call.args[0] for call in mock_sleep.call_args_list), 30.0)

    @mock.patch("breach_scraper.http.time.sleep", return_value=None)
    @mock.patch("breach_scraper.http.urlopen")
    def test_candidate_fallback(self, mock_urlopen: mock.Mock, _sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = [
            HTTPError("u", 403, "Forbidden", None, None),
            _FakeResponse(b"ok"),
        ]
        result = fetch_url(["https://a.test", "https://b.test"], retries=2, backoff=0)
        self.assertEqual(result, "ok")
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_empty_candidates_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            fetch_url([])


if __name__ == "__main__":
    unittest.main()
