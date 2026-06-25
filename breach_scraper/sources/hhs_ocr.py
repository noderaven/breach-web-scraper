"""HHS OCR breach portal connector."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

DEFAULT_URL = "https://ocrportal.hhs.gov/ocr/breach/breach_report_hip.jsf"
ENTITY_TYPES = (
    "Healthcare Provider",
    "Health Plan",
    "Business Associate",
    "Healthcare Clearing House",
)
BREACH_TYPES = (
    "Hacking/IT Incident",
    "Unauthorized Access/Disclosure",
    "Improper Disposal",
    "Unauthorized Access",
    "Theft",
    "Loss",
    "Other",
)
HEADER_TEXT = (
    "Expand AllName of Covered Entity State Covered Entity Type Individuals Affected "
    "Breach Submission Date Type of Breach Location of Breached Information Business Associate Present Web Description"  # noqa: E501
)
CSV_ACTION_RE = re.compile(
    r"onclick=\"mojarra\.jsfcljs\(document\.getElementById\('ocrForm'\),\{'(?P<action>ocrForm:j_idt\d+)':'[^']+'\},''\);return false\">"  # noqa: E501
    r"<img[^>]+alt=\"CSV\"",
    re.IGNORECASE,
)
VIEWSTATE_RE = re.compile(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', re.IGNORECASE)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_date(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    return datetime.strptime(cleaned, "%m/%d/%Y").date().isoformat()


def _normalize_count(value: str) -> str:
    cleaned = _clean_text(value)
    digits = re.sub(r"[^\d]", "", cleaned)
    if not digits:
        return cleaned
    return f"{int(digits):,}"


def _record_sort_key(record: dict[str, str]) -> tuple[int, str, str]:
    reported = record.get("date_reported", "")
    return (1 if reported else 0, reported, record.get("organization_name", ""))


def normalize_record(record: dict[str, str]) -> dict[str, str]:
    normalized = dict(record)
    normalized["date_reported"] = _normalize_date(normalized.get("date_reported", ""))
    normalized["individuals_affected"] = _normalize_count(
        normalized.get("individuals_affected", "")
    )
    return normalized


def normalize_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        (normalize_record(record) for record in records), key=_record_sort_key, reverse=True
    )


class _TextExtractor(HTMLParser):
    BREAK_TAGS = {"br", "p", "div", "li", "tr", "td", "th", "section", "article", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


class _Cell:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def add_text(self, text: str) -> None:
        if text:
            self.parts.append(text)

    @property
    def text(self) -> str:
        return _clean_text("".join(self.parts))


class _ResultsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_tbody = False
        self.in_row = False
        self.in_cell = False
        self.current_row_valid = False
        self.current_row: list[_Cell] = []
        self.current_cell: _Cell | None = None
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tbody" and attrs_dict.get("id") == "ocrForm:reportResultTable_data":
            self.in_tbody = True
            return

        if not self.in_tbody:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
            self.current_row_valid = "data-ri" in attrs_dict
            return

        if self.in_row and tag == "td":
            self.in_cell = True
            self.current_cell = _Cell()
            return

        if self.in_cell and tag == "br" and self.current_cell:
            self.current_cell.add_text(", ")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_tbody:
            return

        if tag == "tbody":
            self.in_tbody = False
            return

        if tag == "td" and self.in_cell and self.current_cell:
            self.current_row.append(self.current_cell)
            self.current_cell = None
            self.in_cell = False
            return

        if tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row_valid and self.current_row:
                self.rows.append([cell.text for cell in self.current_row])

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell:
            self.current_cell.add_text(data)


def _build_headers(user_agent: str | None = None) -> dict[str, str]:
    return {
        "User-Agent": user_agent
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://ocrportal.hhs.gov/",
    }


def fetch_html(
    url: str = DEFAULT_URL,
    *,
    timeout: int = 30,
    start_date: date | None = None,
    end_date: date | None = None,
    user_agent: str | None = None,
    retries: int = 3,
) -> str:
    try:
        opener = build_opener(HTTPCookieProcessor())
        request = Request(url, headers=_build_headers(user_agent))
        with opener.open(request, timeout=timeout) as response:  # nosec B310
            charset = response.headers.get_content_charset() or "utf-8"
            html: str = response.read().decode(charset, errors="replace")

        viewstate_match = VIEWSTATE_RE.search(html)
        csv_action_match = CSV_ACTION_RE.search(html)
        if not viewstate_match or not csv_action_match:
            return html

        action = csv_action_match.group("action")
        data = urlencode(
            {
                "ocrForm": "ocrForm",
                action: action,
                "javax.faces.ViewState": viewstate_match.group(1),
            }
        ).encode("utf-8")
        export_request = Request(
            url,
            data=data,
            headers={**_build_headers(), "Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(export_request, timeout=timeout) as response:  # nosec B310
            charset = response.headers.get_content_charset() or "utf-8"
            exported: str = response.read().decode(charset, errors="replace")
        return exported or html
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Failed to fetch HHS OCR page: {exc}") from exc


def _parse_csv_export(text: str) -> list[dict[str, str]]:
    if not text.lstrip().startswith('"'):
        return []

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []

    records: list[dict[str, str]] = []
    for row in rows[1:]:
        if len(row) < 8:
            continue
        records.append(
            {
                "organization_name": row[0],
                "state": row[1],
                "covered_entity_type": row[2],
                "individuals_affected": row[3],
                "date_reported": row[4],
                "type_of_breach": row[5],
                "location_of_breached_information": row[6],
                "business_associate_present": row[7],
                "breach_description": row[8] if len(row) > 8 else "",
            }
        )

    return records


def _parse_html_table(html: str) -> list[dict[str, str]]:
    parser = _ResultsTableParser()
    parser.feed(html)

    records: list[dict[str, str]] = []
    for row in parser.rows:
        if len(row) < 8:
            continue
        records.append(
            {
                "organization_name": row[1] if len(row) > 1 else "",
                "state": row[2] if len(row) > 2 else "",
                "covered_entity_type": row[3] if len(row) > 3 else "",
                "individuals_affected": row[4] if len(row) > 4 else "",
                "date_reported": row[5] if len(row) > 5 else "",
                "type_of_breach": row[6] if len(row) > 6 else "",
                "location_of_breached_information": row[7] if len(row) > 7 else "",
                "business_associate_present": row[8] if len(row) > 8 else "",
                "breach_description": row[9] if len(row) > 9 else "",
            }
        )

    return records


def _parse_row(line: str) -> dict[str, str] | None:
    line = _clean_text(line)
    if not line:
        return None

    business_associate_present = None
    for candidate in (" Yes", " No"):
        if line.endswith(candidate):
            business_associate_present = candidate.strip()
            line = line[: -len(candidate)].rstrip()
            break
    if not business_associate_present:
        return None

    breach_type = None
    location = ""
    for candidate in BREACH_TYPES:
        marker = f" {candidate} "
        if marker in line:
            before, after = line.split(marker, 1)
            breach_type = candidate
            location = after.strip()
            line = before.strip()
            break
    if not breach_type:
        return None

    match = re.search(r"\s(\d+)\s+(\d{2}/\d{2}/\d{4})$", line)
    if not match:
        return None
    individuals_affected = match.group(1)
    submission_date = match.group(2)
    line = line[: match.start()].rstrip()

    entity_type = None
    for candidate in ENTITY_TYPES:
        marker = f" {candidate}"
        if line.endswith(marker):
            entity_type = candidate
            line = line[: -len(marker)].rstrip()
            break
    if not entity_type:
        return None

    state_match = re.search(r"\s([A-Z]{2})$", line)
    if not state_match:
        return None
    state = state_match.group(1)
    organization_name = line[: state_match.start()].strip()

    return {
        "organization_name": organization_name,
        "state": state,
        "covered_entity_type": entity_type,
        "individuals_affected": individuals_affected,
        "date_reported": submission_date,
        "type_of_breach": breach_type,
        "location_of_breached_information": location,
        "business_associate_present": business_associate_present,
    }


def _parse_flattened_text(html: str) -> list[dict[str, str]]:
    """Fallback for older fixtures and any unexpected non-table renderings."""
    extractor = _TextExtractor()
    extractor.feed(html)
    lines = [_clean_text(line) for line in extractor.text().splitlines()]
    lines = [line for line in lines if line]

    try:
        start_index = lines.index(HEADER_TEXT) + 1
    except ValueError:
        return []

    records: list[dict[str, str]] = []
    for line in lines[start_index:]:
        if line.startswith("(Displaying ") or line.startswith("We are generating the report"):
            break
        record = _parse_row(line)
        if record:
            records.append(record)

    return records


def parse_breach_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    records = _parse_csv_export(html)
    if not records:
        records = _parse_html_table(html)
    if not records:
        records = _parse_flattened_text(html)
    return normalize_records(records)
