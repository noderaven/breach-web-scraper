import unittest
from pathlib import Path

from breach_scraper.wa_atg_scraper import parse_breach_table, to_markdown


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


if __name__ == "__main__":
    unittest.main()
