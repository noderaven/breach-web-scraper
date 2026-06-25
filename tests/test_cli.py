from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from breach_scraper.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "wa_atg_sample.html"
# Fixture records are from 2024; use an explicit window that includes them.
RANGE = ["--start-date", "2024-01-01", "--end-date", "2024-12-31"]


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = mock.Mock()
        self.headers.get_content_charset.return_value = "utf-8"

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class TestCliOffline(unittest.TestCase):
    def test_offline_json_annotates_source(self) -> None:
        rc, out, _ = run(["--input-html", str(FIXTURE), "--output", "json", *RANGE])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["source"], "Washington Attorney General")
        self.assertEqual(
            {r["organization_name"] for r in data},
            {"Acme Corp", "Northwest LLC"},
        )

    def test_offline_markdown_header(self) -> None:
        rc, out, _ = run(["--input-html", str(FIXTURE), "--output", "markdown", *RANGE])
        self.assertEqual(rc, 0)
        self.assertIn("| Source | Date Reported | Organization Name", out)

    def test_default_window_filters_old_records(self) -> None:
        # Default range is the last 6 months; the 2024 fixture rows fall outside it.
        rc, out, _ = run(["--input-html", str(FIXTURE), "--output", "json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])


class TestCliOnline(unittest.TestCase):
    @mock.patch("breach_scraper.http.urlopen")
    def test_online_pipeline(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value = _FakeResponse(FIXTURE.read_bytes())
        rc, out, _ = run(["--output", "json", *RANGE])
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)), 2)


class TestCliMisc(unittest.TestCase):
    def test_list_sources(self) -> None:
        rc, out, _ = run(["--list-sources"])
        self.assertEqual(rc, 0)
        self.assertIn("wa_atg", out)

    def test_unknown_source_errors(self) -> None:
        rc, _, err = run(["--source", "nope", *RANGE])
        self.assertEqual(rc, 1)
        self.assertIn("Unknown source", err)

    def test_bad_start_date_errors(self) -> None:
        rc, _, err = run(["--start-date", "not-a-date"])
        self.assertEqual(rc, 1)
        self.assertIn("Error:", err)


if __name__ == "__main__":
    unittest.main()
