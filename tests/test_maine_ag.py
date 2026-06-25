import json
import unittest

from breach_scraper.sources.maine_ag import extract_detail_links, parse_breach_table

LIST_HTML = """
<html>
  <body>
    <ul>
      <li><a href="11111111-1111-1111-1111-111111111111.html">Example Hospital</a></li>
      <li><a href="22222222-2222-2222-2222-222222222222.html">Example County</a></li>
    </ul>
  </body>
</html>
"""


DETAIL_HTML = """
<html>
  <body>
    <h1>Data Breach Notifications</h1>
    <h2>Entity Information</h2>
    <ul>
      <li>Type of Organization: Healthcare</li>
      <li>Entity Name: Example Hospital</li>
    </ul>
    <h2>Breach Information</h2>
    <ul>
      <li>Total number of persons affected (including residents): 2068</li>
      <li>Total number of Maine residents affected: 1733</li>
      <li>Date(s) Breach Occured: 11/22/2024</li>
      <li>Date Breach Discovered: 07-01-2025</li>
      <li>Description of the Breach:
        <ul>
          <li>External system breach (hacking)</li>
        </ul>
      </li>
      <li>Information Acquired - Name or other personal identifier in combination with:
        <ul>
          <li>Social Security Number</li>
          <li>Medical Information</li>
        </ul>
      </li>
    </ul>
    <h2>Notification and Protection Services</h2>
    <ul>
      <li>Type of Notification: Written</li>
      <li>Date(s) of consumer notification: 08/08/2025</li>
      <li>Copy of notice to affected Maine residents:
        <a href="docs/example-notice.pdf">Example Notice</a>
      </li>
    </ul>
  </body>
</html>
"""

MALFORMED_DATE_DETAIL_HTML = """
<html>
  <body>
    <h1>Data Breach Notifications</h1>
    <h2>Entity Information</h2>
    <ul>
      <li>Type of Organization: Financial Services</li>
      <li>Entity Name: Example CPA</li>
    </ul>
    <h2>Notification and Protection Services</h2>
    <ul>
      <li>Date(s) of consumer notification: 03/17/2-26</li>
    </ul>
    <h2>Breach Information</h2>
    <ul>
      <li>Date(s) Breach Occured: 03132026</li>
    </ul>
  </body>
</html>
"""


class TestMaineAgScraper(unittest.TestCase):
    def test_extract_detail_links_finds_uuid_detail_pages(self) -> None:
        links = extract_detail_links(
            LIST_HTML,
            base_url="https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/list.html",
        )

        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]["text"], "Example Hospital")
        self.assertTrue(links[0]["url"].endswith("11111111-1111-1111-1111-111111111111.html"))

    def test_parse_breach_table_parses_detail_bundle(self) -> None:
        payload = json.dumps(
            {
                "list_url": "https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/list.html",
                "detail_pages": [
                    {
                        "url": "https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/11111111-1111-1111-1111-111111111111.html",
                        "html": DETAIL_HTML,
                    }
                ],
            }
        )

        records = parse_breach_table(
            payload,
            base_url="https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/list.html",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["organization_name"], "Example Hospital")
        self.assertEqual(records[0]["date_of_breach"], "2024-11-22")
        self.assertEqual(records[0]["date_reported"], "2025-08-08")
        self.assertEqual(records[0]["number_of_maine_residents_affected"], "1,733")
        self.assertEqual(records[0]["total_persons_affected"], "2,068")
        self.assertEqual(
            records[0]["notice_url"],
            "https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/docs/example-notice.pdf",
        )
        self.assertIn("Social Security Number", records[0]["information_compromised"])

    def test_parse_breach_table_can_fall_back_to_list_page_links(self) -> None:
        records = parse_breach_table(
            LIST_HTML,
            base_url="https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/list.html",
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["organization_name"], "Example Hospital")
        self.assertTrue(records[0]["organization_name_url"].endswith(".html"))

    def test_parse_breach_table_repairs_malformed_live_dates(self) -> None:
        payload = json.dumps(
            {
                "list_url": "https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/list.html",
                "detail_pages": [
                    {
                        "url": "https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/33333333-3333-3333-3333-333333333333.html",
                        "html": MALFORMED_DATE_DETAIL_HTML,
                    }
                ],
            }
        )

        records = parse_breach_table(payload)

        self.assertEqual(records[0]["date_of_breach"], "2026-03-13")
        self.assertEqual(records[0]["date_reported"], "2026-03-17")


if __name__ == "__main__":
    unittest.main()
