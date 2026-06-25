from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.client import BadStatusLine
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

from breach_scraper.wa_atg_scraper import fetch_html, main, parse_breach_table, to_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "wa_atg_sample.html"


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


class TestParsing(unittest.TestCase):
    def test_parse_breach_table_extracts_rows_and_links(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        records = parse_breach_table(
            html, base_url="https://www.atg.wa.gov/data-breach-notifications"
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["date_reported"], "2024-06-01")
        self.assertEqual(records[0]["organization_name"], "Acme Corp")
        self.assertEqual(records[0]["notice"], "Letter")
        self.assertEqual(records[0]["notice_url"], "https://www.atg.wa.gov/docs/acme-notice.pdf")

    def test_to_markdown_formats_table(self) -> None:
        markdown = to_markdown([{"date_reported": "2024-01-01", "organization_name": "Example"}])
        self.assertIn("| date_reported | organization_name |", markdown)
        self.assertIn("| 2024-01-01 | Example |", markdown)


class TestFetchHtml(unittest.TestCase):
    @mock.patch("breach_scraper.wa_atg_scraper.time.sleep", return_value=None)
    @mock.patch("breach_scraper.wa_atg_scraper.urlopen")
    def test_retries_then_succeeds(self, mock_urlopen: mock.Mock, _sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = [URLError("boom"), _FakeResponse(b"<html>ok</html>")]
        html = fetch_html("https://example.test", retries=3, backoff=0)
        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(mock_urlopen.call_count, 2)

    @mock.patch("breach_scraper.wa_atg_scraper.time.sleep", return_value=None)
    @mock.patch("breach_scraper.wa_atg_scraper.urlopen")
    def test_retries_exhausted_raises(self, mock_urlopen: mock.Mock, _sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = URLError("down")
        with self.assertRaises(RuntimeError):
            fetch_html("https://example.test", retries=2, backoff=0)
        self.assertEqual(mock_urlopen.call_count, 2)

    @mock.patch("breach_scraper.wa_atg_scraper.urlopen")
    def test_http_403_raises_actionable_error(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.side_effect = HTTPError("https://example.test", 403, "Forbidden", None, None)
        with self.assertRaises(RuntimeError) as ctx:
            fetch_html("https://example.test", retries=3, backoff=0)
        message = str(ctx.exception)
        self.assertIn("403", message)
        self.assertIn("--input-html", message)
        self.assertEqual(mock_urlopen.call_count, 1)

    @mock.patch("breach_scraper.wa_atg_scraper.time.sleep", return_value=None)
    @mock.patch("breach_scraper.wa_atg_scraper.urlopen")
    def test_retries_on_http_protocol_error(
        self, mock_urlopen: mock.Mock, _sleep: mock.Mock
    ) -> None:
        mock_urlopen.side_effect = [BadStatusLine("oops"), _FakeResponse(b"<html>ok</html>")]
        html = fetch_html("https://example.test", retries=3, backoff=0)
        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(mock_urlopen.call_count, 2)

    @mock.patch("breach_scraper.wa_atg_scraper.urlopen")
    def test_unknown_charset_falls_back_to_utf8(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value = _FakeResponse(
            b"<html>caf\xc3\xa9</html>", charset="bogus-charset"
        )
        html = fetch_html("https://example.test", retries=1, backoff=0)
        self.assertIn("caf", html)

    @mock.patch("breach_scraper.wa_atg_scraper.time.sleep")
    @mock.patch("breach_scraper.wa_atg_scraper.urlopen")
    def test_backoff_is_capped(self, mock_urlopen: mock.Mock, mock_sleep: mock.Mock) -> None:
        mock_urlopen.side_effect = URLError("down")
        with self.assertRaises(RuntimeError):
            fetch_html("https://example.test", retries=20, backoff=1.0)
        max_sleep = max(call.args[0] for call in mock_sleep.call_args_list)
        self.assertLessEqual(max_sleep, 30.0)


class TestMainCli(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_input_html_offline_json(self) -> None:
        rc, output, _ = self._run(["--input-html", str(FIXTURE), "--output", "json"])
        self.assertEqual(rc, 0)
        data = json.loads(output)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["organization_name"], "Acme Corp")

    def test_input_html_offline_markdown(self) -> None:
        rc, output, _ = self._run(["--input-html", str(FIXTURE), "--output", "markdown"])
        self.assertEqual(rc, 0)
        self.assertIn("| date_reported | organization_name | notice | notice_url |", output)

    def test_input_html_offline_csv(self) -> None:
        rc, output, _ = self._run(["--input-html", str(FIXTURE), "--output", "csv"])
        self.assertEqual(rc, 0)
        self.assertIn("date_reported,organization_name", output)
        self.assertIn("2024-06-01,Acme Corp", output)

    def test_limit_truncates(self) -> None:
        rc, output, _ = self._run(
            ["--input-html", str(FIXTURE), "--output", "json", "--limit", "1"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(output)), 1)

    def test_missing_input_file_returns_error_code(self) -> None:
        rc, _, err = self._run(["--input-html", "does-not-exist.html"])
        self.assertEqual(rc, 1)
        self.assertIn("error:", err)

    def test_malformed_url_returns_error_code(self) -> None:
        rc, _, err = self._run(["--url", "not a url"])
        self.assertEqual(rc, 1)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
