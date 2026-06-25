import unittest

from breach_scraper.sources.hhs_ocr import parse_breach_table

HHS_HTML = """
<html>
  <body>
    <div>Breach Report Results</div>
    <div>Expand AllName of Covered Entity State Covered Entity Type Individuals Affected Breach Submission Date Type of Breach Location of Breached Information Business Associate Present Web Description</div>
    <div>Manhattan Retirement Foundation d/b/a Meadowlark Hills KS Healthcare Provider 14442 02/26/2026 Hacking/IT Incident Network Server No</div>
    <div>Commonwealth Care Alliance MA Health Plan 634 02/25/2026 Unauthorized Access/Disclosure Paper/Films No</div>
    <div>44North MI Business Associate 2158 02/16/2026 Hacking/IT Incident Desktop Computer Yes</div>
    <div>(Displaying 1 - 100 of 728)</div>
  </body>
</html>
"""

HHS_CSV = """"javax.faces.component.UIPanel@22cd6ba5","State","Covered Entity Type","Individuals Affected","Breach Submission Date","Type of Breach","Location of Breached Information","javax.faces.component.UIPanel@7ef7c58e","Web Description"
"Manhattan Retirement Foundation d/b/a Meadowlark Hills","KS","Healthcare Provider","14442","02/26/2026","Hacking/IT Incident","Network Server","No",""
"Commonwealth Care Alliance","MA","Health Plan","634","02/25/2026","Unauthorized Access/Disclosure","Paper/Films","No",""
"44North","MI","Business Associate","2158","02/16/2026","Hacking/IT Incident","Desktop Computer","Yes",""
"""


class TestHhsOcrScraper(unittest.TestCase):
    def test_parse_breach_table_extracts_rows(self) -> None:
        records = parse_breach_table(HHS_HTML)

        self.assertEqual(len(records), 3)
        self.assertEqual(
            records[0]["organization_name"],
            "Manhattan Retirement Foundation d/b/a Meadowlark Hills",
        )
        self.assertEqual(records[0]["state"], "KS")
        self.assertEqual(records[0]["covered_entity_type"], "Healthcare Provider")
        self.assertEqual(records[0]["individuals_affected"], "14,442")
        self.assertEqual(records[0]["date_reported"], "2026-02-26")
        self.assertEqual(records[0]["type_of_breach"], "Hacking/IT Incident")
        self.assertEqual(records[0]["location_of_breached_information"], "Network Server")
        self.assertEqual(records[0]["business_associate_present"], "No")

    def test_parse_breach_table_handles_business_associate_rows(self) -> None:
        records = parse_breach_table(HHS_HTML)

        business_associate = next(
            record for record in records if record["organization_name"] == "44North"
        )
        self.assertEqual(business_associate["covered_entity_type"], "Business Associate")
        self.assertEqual(business_associate["business_associate_present"], "Yes")

    def test_parse_breach_table_handles_csv_export(self) -> None:
        records = parse_breach_table(HHS_CSV)

        self.assertEqual(len(records), 3)
        self.assertEqual(
            records[0]["organization_name"],
            "Manhattan Retirement Foundation d/b/a Meadowlark Hills",
        )
        self.assertEqual(records[0]["individuals_affected"], "14,442")


if __name__ == "__main__":
    unittest.main()
