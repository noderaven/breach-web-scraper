import unittest

from breach_scraper.sources.or_doj import _normalize_dateish, parse_breach_table

OREGON_HTML = """
<html>
  <body>
    <h1>Search Data Breaches</h1>
    <div>Organization  Reported Date  Dates of Breach  Dates of Discovery  Date Notice Sent  Number Affected</div>
    <div>Pyramid Advisors Limited Partnership d/b/a Pyramid Global Hospitality  02/25/2026 8/13/2025 - 8/14/2025  8/14/2025</div>
    <div>10/29/2025</div>
    <div>2/25/2026</div>
    <div>139899</div>
    <div>Adapt Oregon Health Care  02/24/2026 1/1/2001  1/1/2001</div>
    <div>1/1/2001</div>
    <div>2908</div>
    <div>Decisely Insurance Services  12/30/2025 12/15/2024 - 12/17/2024  12/17/2024</div>
    <div>1/1/0001</div>
    <div>6/13/2025</div>
    <div>7/15/2025</div>
    <div>261155</div>
  </body>
</html>
"""

OREGON_TABLE_HTML = """
<html>
  <body>
    <table id="grid" class="webgrid-table">
      <thead>
        <tr>
          <th>Organization</th>
          <th>Reported Date</th>
          <th>Dates of Breach</th>
          <th>Dates of Discovery</th>
          <th>Date Notice Sent</th>
          <th>Number Affected</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>Pyramid Advisors Limited Partnership d/b/a Pyramid Global Hospitality</td>
          <td>02/25/2026</td>
          <td>8/13/2025 - 8/14/2025</td>
          <td>8/14/2025<br></td>
          <td>10/29/2025<br>2/25/2026<br></td>
          <td>139899</td>
        </tr>
      </tbody>
    </table>
  </body>
</html>
"""


class TestOregonDojScraper(unittest.TestCase):
    def test_parse_breach_table_extracts_rows_and_normalizes_dates(self) -> None:
        records = parse_breach_table(OREGON_HTML)

        self.assertEqual(len(records), 3)
        self.assertEqual(
            records[0]["organization_name"],
            "Pyramid Advisors Limited Partnership d/b/a Pyramid Global Hospitality",
        )
        self.assertEqual(records[0]["date_reported"], "2026-02-25")
        self.assertEqual(records[0]["date_of_breach"], "2025-08-13 to 2025-08-14")
        self.assertEqual(records[0]["date_of_discovery"], "2025-08-14")
        self.assertEqual(records[0]["date_notice_sent"], "2025-10-29, 2026-02-25")
        self.assertEqual(records[0]["number_affected"], "139,899")

    def test_parse_breach_table_drops_placeholder_dates(self) -> None:
        records = parse_breach_table(OREGON_HTML)

        adapt = next(
            record
            for record in records
            if record["organization_name"] == "Adapt Oregon Health Care"
        )
        self.assertEqual(adapt["date_of_breach"], "")
        self.assertEqual(adapt["date_of_discovery"], "")
        self.assertEqual(adapt["date_notice_sent"], "")
        self.assertEqual(adapt["number_affected"], "2,908")

        decisely = next(
            record
            for record in records
            if record["organization_name"] == "Decisely Insurance Services"
        )
        self.assertEqual(decisely["date_notice_sent"], "2025-06-13, 2025-07-15")

    def test_parse_breach_table_handles_live_table_markup(self) -> None:
        records = parse_breach_table(OREGON_TABLE_HTML)

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["organization_name"],
            "Pyramid Advisors Limited Partnership d/b/a Pyramid Global Hospitality",
        )
        self.assertEqual(records[0]["date_notice_sent"], "2025-10-29, 2026-02-25")
        self.assertEqual(records[0]["number_affected"], "139,899")


class TestOrDojDateNormalization(unittest.TestCase):
    def test_tight_range_is_split(self) -> None:
        self.assertEqual(_normalize_dateish("8/13/2025-8/14/2025"), "2025-08-13 to 2025-08-14")

    def test_invalid_date_returns_original(self) -> None:
        self.assertEqual(_normalize_dateish("13/45/2025"), "13/45/2025")


if __name__ == "__main__":
    unittest.main()
