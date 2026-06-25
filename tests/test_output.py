from __future__ import annotations

import unittest

from breach_scraper.output import (
    _html_affected_count,
    _html_notice_link,
    _html_organization_link,
)


class TestHtmlSafety(unittest.TestCase):
    def test_notice_link_rejects_javascript_scheme(self) -> None:
        self.assertEqual(_html_notice_link({"notice_url": "javascript:alert(1)"}), "")

    def test_notice_link_allows_https(self) -> None:
        link = _html_notice_link({"notice_url": "https://example.gov/notice.pdf"})
        self.assertIn('href="https://example.gov/notice.pdf"', link)

    def test_organization_link_rejects_javascript_scheme(self) -> None:
        out = _html_organization_link(
            {"organization_name": "Acme", "organization_name_url": "javascript:alert(1)"}
        )
        self.assertEqual(out, "Acme")
        self.assertNotIn("href", out)


class TestAffectedCount(unittest.TestCase):
    def test_zero_is_not_skipped(self) -> None:
        self.assertEqual(_html_affected_count({"persons_affected": 0}), "0")

    def test_empty_falls_through(self) -> None:
        self.assertEqual(
            _html_affected_count({"persons_affected": "", "number_affected": "5"}), "5"
        )


if __name__ == "__main__":
    unittest.main()
