from __future__ import annotations

import unittest
from pathlib import Path

from breach_scraper.sources.wa_atg import normalize_record, parse_breach_table

FIXTURE = Path(__file__).parent / "fixtures" / "wa_atg_sample.html"


class TestWaAtg(unittest.TestCase):
    def test_parse_sorts_desc_and_extracts_links(self) -> None:
        records = parse_breach_table(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(records), 2)
        # normalize_records sorts by date descending
        self.assertEqual(records[0]["organization_name"], "Northwest LLC")
        self.assertEqual(records[1]["organization_name"], "Acme Corp")
        self.assertEqual(records[1]["date_reported"], "2024-06-01")
        self.assertEqual(
            records[1]["notice_url"],
            "https://www.atg.wa.gov/docs/acme-notice.pdf",
        )

    def test_normalize_record_dates_and_counts(self) -> None:
        out = normalize_record(
            {"date_reported": "6/1/2024", "number_of_washingtonians_affected": "1234"}
        )
        self.assertEqual(out["date_reported"], "2024-06-01")
        self.assertEqual(out["number_of_washingtonians_affected"], "1,234")

    def test_normalize_count_keeps_ranges_intact(self) -> None:
        out = normalize_record({"number_of_washingtonians_affected": "1,200 - 1,500"})
        self.assertEqual(out["number_of_washingtonians_affected"], "1,200 - 1,500")


if __name__ == "__main__":
    unittest.main()
