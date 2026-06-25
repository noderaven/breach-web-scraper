"""Oregon Department of Justice breach notification connector."""

from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser

from breach_scraper.http import fetch_url

DEFAULT_URL = "https://justice.oregon.gov/consumer/databreach/"
HEADER_TEXT = (
    "Organization Reported Date Dates of Breach Dates of Discovery Date Notice Sent Number Affected"
)
SENTINEL_DATES = {"1/1/0001", "01/01/0001", "1/1/2001", "01/01/2001"}
COUNT_FIELDS = ("number_affected",)
DATE_FIELDS = ("date_reported", "date_of_breach", "date_of_discovery", "date_notice_sent")
HEADER_MAP = {
    "organization": "organization_name",
    "reported_date": "date_reported",
    "dates_of_breach": "date_of_breach",
    "dates_of_discovery": "date_of_discovery",
    "date_notice_sent": "date_notice_sent",
    "number_affected": "number_affected",
}
ROW_START_RE = re.compile(
    r"^(?P<organization>.+?)\s+"
    r"(?P<reported>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<breach>.+?)\s+"
    r"(?P<discovery>(?:\d{1,2}/\d{1,2}/\d{4}|1/1/0001|1/1/2001)(?:\s*-\s*(?:\d{1,2}/\d{1,2}/\d{4}|1/1/0001|1/1/2001))?)$"
)
DATE_TOKEN_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}|1/1/0001|1/1/2001", re.IGNORECASE)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_count(value: str) -> str:
    cleaned = _clean_text(value)
    digits = re.sub(r"[^\d]", "", cleaned)
    if not digits:
        return cleaned
    return f"{int(digits):,}"


def _normalize_single_date(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned or cleaned in SENTINEL_DATES:
        return ""
    try:
        return datetime.strptime(cleaned, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return cleaned


def _normalize_dateish(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""

    normalized_parts: list[str] = []
    for part in [segment.strip() for segment in cleaned.split(",")]:
        if not part:
            continue

        if "-" in part:
            range_parts = [_normalize_single_date(item) for item in re.split(r"\s*-\s*", part)]
            range_parts = [item for item in range_parts if item]
            if range_parts:
                normalized_parts.append(" to ".join(range_parts))
            continue

        normalized = _normalize_single_date(part)
        if normalized:
            normalized_parts.append(normalized)

    return ", ".join(normalized_parts)


def _record_sort_key(record: dict[str, str]) -> tuple[int, str, str]:
    reported_date = record.get("date_reported", "")
    primary_date = reported_date or record.get("date_of_breach", "")
    return (1 if primary_date else 0, primary_date, record.get("organization_name", ""))


def normalize_record(record: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in record.items():
        cleaned = _clean_text(value)
        if key in DATE_FIELDS:
            cleaned = _normalize_dateish(cleaned)
        elif key in COUNT_FIELDS:
            cleaned = _normalize_count(cleaned)
        normalized[key] = cleaned
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


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_tag: str | None = None
        self.current_row: list[tuple[str, _Cell]] = []
        self.current_cell: _Cell | None = None
        self.rows: list[tuple[str, list[_Cell]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and attrs_dict.get("id") == "grid":
            self.in_table = True
            return

        if not self.in_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
            return

        if self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.current_tag = tag
            self.current_cell = _Cell()
            return

        if self.in_cell and tag == "br" and self.current_cell:
            self.current_cell.add_text(", ")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_table:
            return

        if tag == "table":
            self.in_table = False
            return

        if tag in {"th", "td"} and self.in_cell and self.current_cell and self.current_tag == tag:
            self.current_row.append((tag, self.current_cell))
            self.in_cell = False
            self.current_tag = None
            self.current_cell = None
            return

        if tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                row_type = (
                    "header"
                    if all(cell_tag == "th" for cell_tag, _ in self.current_row)
                    else "data"
                )
                self.rows.append((row_type, [cell for _, cell in self.current_row]))

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell:
            self.current_cell.add_text(data)


def fetch_html(
    url: str = DEFAULT_URL,
    *,
    timeout: int = 30,
    start_date: date | None = None,
    end_date: date | None = None,
    user_agent: str | None = None,
    retries: int = 3,
) -> str:
    return fetch_url(
        [url],
        timeout=timeout,
        retries=retries,
        user_agent=user_agent,
        headers={"Referer": "https://justice.oregon.gov/"},
    )


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _clean_text(value).lower()).strip("_")


def _parse_html_table(html: str) -> list[dict[str, str]]:
    parser = _TableParser()
    parser.feed(html)

    headers: list[str] = []
    records: list[dict[str, str]] = []

    for row_type, row in parser.rows:
        if row_type == "header" and not headers:
            headers = [
                HEADER_MAP.get(_header_key(cell.text), _header_key(cell.text)) for cell in row
            ]
            continue

        if row_type != "data" or not headers:
            continue

        values: dict[str, str] = {}
        for idx, cell in enumerate(row):
            if idx >= len(headers):
                continue
            values[headers[idx]] = cell.text

        if values.get("organization_name"):
            records.append(values)

    return records


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
    current: dict[str, str] | None = None
    notice_dates: list[str] = []

    for line in lines[start_index:]:
        if line.startswith("Page ") or line.startswith("Next") or line.startswith("Previous"):
            break

        match = ROW_START_RE.match(line)
        if match:
            if current:
                current["date_notice_sent"] = ", ".join(notice_dates)
                records.append(current)
            current = {
                "organization_name": match.group("organization"),
                "date_reported": match.group("reported"),
                "date_of_breach": match.group("breach"),
                "date_of_discovery": match.group("discovery"),
            }
            notice_dates = []
            continue

        if current is None:
            continue

        if re.fullmatch(r"\d[\d,]*", line):
            current["number_affected"] = line
            current["date_notice_sent"] = ", ".join(notice_dates)
            records.append(current)
            current = None
            notice_dates = []
            continue

        if DATE_TOKEN_RE.search(line):
            notice_dates.append(line)

    if current:
        current["date_notice_sent"] = ", ".join(notice_dates)
        records.append(current)

    return records


def parse_breach_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    records = _parse_html_table(html)
    if not records:
        records = _parse_flattened_text(html)
    return normalize_records(records)
