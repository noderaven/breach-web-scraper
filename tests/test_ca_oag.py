import json
import unittest

from breach_scraper.sources.ca_oag import parse_breach_table, parse_list_table

LIST_HTML = """
<html>
  <body>
    <table>
      <thead>
        <tr>
          <th>Organization Name</th>
          <th>Date(s) of Breach</th>
          <th>Reported Date</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><a href="/ecrime/databreach/reports/sb24-619986">OSI Systems, Inc.</a></td>
          <td>12/23/2025</td>
          <td>03/11/2026</td>
        </tr>
        <tr>
          <td><a href="/ecrime/databreach/reports/sb24-619985">SitusAMC Holdings Corporation</a></td>
          <td>11/13/2025, 11/21/2025</td>
          <td>03/10/2026</td>
        </tr>
      </tbody>
    </table>
  </body>
</html>
"""

DIV_LIST_HTML = """
<html>
  <body>
    <div class="views-row">
      <div class="views-field views-field-title">
        <a href="/ecrime/databreach/reports/sb24-619986">OSI Systems, Inc.</a>
      </div>
      <div class="views-field views-field-field-breach-date">12/23/2025</div>
      <div class="views-field views-field-field-reported-date">03/11/2026</div>
    </div>
    <div class="views-row">
      <div class="views-field views-field-title">
        <a href="/ecrime/databreach/reports/sb24-619985">SitusAMC Holdings Corporation</a>
      </div>
      <div class="views-field views-field-field-breach-date">11/13/2025, 11/21/2025</div>
      <div class="views-field views-field-field-reported-date">03/10/2026</div>
    </div>
  </body>
</html>
"""


DETAIL_HTML = """
<html>
  <body>
    <h1>Submitted Breach Notification Sample</h1>
    <p>Sample of Notice:</p>
    <a href="/system/files/attachments/press-docs/osi_notice.pdf">OSI Systems Inc. - Sample Notice.pdf</a>
    <p>Organization Name:</p>
    <p>OSI Systems, Inc.</p>
    <p>Date(s) of Breach (if known):</p>
    <p>Tuesday, December 23, 2025</p>
  </body>
</html>
"""


class TestCaliforniaOagScraper(unittest.TestCase):
    def test_parse_list_table_extracts_rows(self) -> None:
        records = parse_list_table(LIST_HTML, base_url="https://oag.ca.gov/privacy/databreach/list")

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["organization_name"], "OSI Systems, Inc.")
        self.assertEqual(
            records[0]["organization_name_url"],
            "https://oag.ca.gov/ecrime/databreach/reports/sb24-619986",
        )
        self.assertEqual(records[0]["date_of_breach"], "12/23/2025")
        self.assertEqual(records[0]["date_reported"], "03/11/2026")

    def test_parse_list_table_extracts_rows_from_div_layout(self) -> None:
        records = parse_list_table(
            DIV_LIST_HTML, base_url="https://oag.ca.gov/privacy/databreach/list"
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["organization_name"], "OSI Systems, Inc.")
        self.assertEqual(records[0]["date_of_breach"], "12/23/2025")
        self.assertEqual(records[0]["date_reported"], "03/11/2026")
        self.assertEqual(records[1]["date_of_breach"], "11/13/2025, 11/21/2025")

    def test_parse_breach_table_merges_notice_link_from_detail_pages(self) -> None:
        payload = json.dumps(
            {
                "list_url": "https://oag.ca.gov/privacy/databreach/list",
                "records": parse_list_table(
                    DIV_LIST_HTML, base_url="https://oag.ca.gov/privacy/databreach/list"
                ),
                "detail_pages": [
                    {
                        "url": "https://oag.ca.gov/ecrime/databreach/reports/sb24-619986",
                        "html": DETAIL_HTML,
                    }
                ],
            }
        )

        records = parse_breach_table(payload, base_url="https://oag.ca.gov/privacy/databreach/list")

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["organization_name"], "OSI Systems, Inc.")
        self.assertEqual(records[0]["date_of_breach"], "2025-12-23")
        self.assertEqual(records[0]["date_reported"], "2026-03-11")
        self.assertEqual(
            records[0]["notice_url"],
            "https://oag.ca.gov/system/files/attachments/press-docs/osi_notice.pdf",
        )
        self.assertEqual(records[0]["notice"], "OSI Systems Inc. - Sample Notice.pdf")


if __name__ == "__main__":
    unittest.main()
