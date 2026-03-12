import io
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from scraper import wa_atg_scraper
from scraper.wa_atg_scraper import _candidate_urls, parse_breach_table, to_markdown


FIXTURE = Path(__file__).parent / "fixtures" / "wa_atg_sample.html"


class TestWaatgScraper(unittest.TestCase):
    def test_parse_breach_table_extracts_rows_and_links(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")

        records = parse_breach_table(html, base_url="https://www.atg.wa.gov/data-breach-notifications")

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["date_reported"], "2024-06-01")
        self.assertEqual(records[0]["organization_name"], "Acme Corp")
        self.assertEqual(records[0]["notice"], "Letter")
        self.assertEqual(
            records[0]["notice_url"],
            "https://www.atg.wa.gov/docs/acme-notice.pdf",
        )

    def test_to_markdown_formats_table(self) -> None:
        markdown = to_markdown([
            {"date_reported": "2024-01-01", "organization_name": "Example"}
        ])

        self.assertIn("| date_reported | organization_name |", markdown)
        self.assertIn("| 2024-01-01 | Example |", markdown)

    def test_candidate_urls_contains_www_and_non_www(self) -> None:
        candidates = _candidate_urls("https://www.atg.wa.gov/data-breach-notifications")
        self.assertIn("https://www.atg.wa.gov/data-breach-notifications", candidates)
        self.assertIn("https://atg.wa.gov/data-breach-notifications", candidates)

    def test_main_supports_input_html(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as handle:
            handle.write(FIXTURE.read_text(encoding="utf-8"))
            path = handle.name

        try:
            buffer = io.StringIO()
            original_stdout = wa_atg_scraper.sys.stdout
            wa_atg_scraper.sys.stdout = buffer
            try:
                exit_code = wa_atg_scraper.main(["--input-html", path, "--output", "json", "--limit", "1"])
            finally:
                wa_atg_scraper.sys.stdout = original_stdout
            self.assertEqual(exit_code, 0)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_fetch_html_403_has_helpful_message(self) -> None:
        original = wa_atg_scraper.urlopen

        def _raise_http_error(*_args, **_kwargs):
            raise HTTPError("https://www.atg.wa.gov/data-breach-notifications", 403, "Forbidden", {}, None)

        wa_atg_scraper.urlopen = _raise_http_error
        try:
            with self.assertRaises(RuntimeError) as context:
                wa_atg_scraper.fetch_html()
            self.assertIn("Request blocked with HTTP 403", str(context.exception))
        finally:
            wa_atg_scraper.urlopen = original


if __name__ == "__main__":
    unittest.main()
