"""Washington Attorney General breach notification source."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from breach_scraper.http import fetch_url

DEFAULT_URL = "https://www.atg.wa.gov/data-breach-notifications"
DATE_FIELDS = ("date_reported", "date_of_breach")
COUNT_FIELDS = ("number_of_washingtonians_affected",)
PREFERRED_FIELD_ORDER = (
    "date_reported",
    "organization_name",
    "organization_name_url",
    "date_of_breach",
    "number_of_washingtonians_affected",
    "information_compromised",
)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _slugify(header: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    return normalized or "column"


def _normalize_date(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return cleaned


def _normalize_count(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    digits = re.sub(r"[^\d]", "", cleaned)
    if not digits:
        return cleaned
    return f"{int(digits):,}"


def _field_sort_key(item: tuple[str, str]) -> tuple[int, int | str]:
    key, _ = item
    if key in PREFERRED_FIELD_ORDER:
        return (0, PREFERRED_FIELD_ORDER.index(key))
    return (1, key)


def _record_sort_key(record: dict[str, str]) -> tuple[int, str, str, str]:
    date_of_breach = record.get("date_of_breach", "")
    date_reported = record.get("date_reported", "")
    primary_date = date_of_breach or date_reported
    return (
        1 if primary_date else 0,
        primary_date,
        date_reported,
        record.get("organization_name", ""),
    )


def normalize_record(record: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in record.items():
        cleaned = _clean_text(value)
        if key in DATE_FIELDS:
            cleaned = _normalize_date(cleaned)
        elif key in COUNT_FIELDS:
            cleaned = _normalize_count(cleaned)
        normalized[key] = cleaned
    return dict(sorted(normalized.items(), key=_field_sort_key))


def normalize_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized = [normalize_record(record) for record in records]
    return sorted(normalized, key=_record_sort_key, reverse=True)


@dataclass
class _Cell:
    text_chunks: list[str] = field(default_factory=list)
    href: str | None = None

    def add_text(self, text: str) -> None:
        if text:
            self.text_chunks.append(text)

    @property
    def text(self) -> str:
        return _clean_text(" ".join(self.text_chunks))


class _ATGBreachTableParser(HTMLParser):
    """Extract the first meaningful HTML table from the page."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.in_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.current_cell_tag: str | None = None
        self.current_row: list[tuple[str, _Cell]] = []
        self.current_cell: _Cell | None = None
        self.tables: list[list[tuple[str, list[tuple[str, _Cell]]]]] = []
        self.current_table: list[tuple[str, list[tuple[str, _Cell]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)

        if tag == "table":
            if not self.in_table:
                self.in_table = True
                self.current_table = []
            self.table_depth += 1
            return

        if not self.in_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
            return

        if self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.current_cell_tag = tag
            self.current_cell = _Cell()
            return

        if self.in_cell and tag == "a" and self.current_cell:
            href = attrs_dict.get("href")
            if href and not self.current_cell.href:
                self.current_cell.href = urljoin(self.base_url, href)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_table:
            return

        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_table = False
                if self.current_table:
                    self.tables.append(self.current_table)
            return

        if (
            tag in {"th", "td"}
            and self.in_cell
            and self.current_cell
            and self.current_cell_tag == tag
        ):
            self.current_row.append((tag, self.current_cell))
            self.in_cell = False
            self.current_cell_tag = None
            self.current_cell = None
            return

        if tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                is_header = all(cell_tag == "th" for cell_tag, _ in self.current_row)
                row_type = "header" if is_header else "data"
                self.current_table.append((row_type, self.current_row))

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell:
            self.current_cell.add_text(data)


def _candidate_urls(url: str) -> list[str]:
    candidates = [url]
    if "www.atg.wa.gov" in url:
        candidates.append(url.replace("www.atg.wa.gov", "atg.wa.gov"))
    elif "atg.wa.gov" in url:
        candidates.append(url.replace("atg.wa.gov", "www.atg.wa.gov"))
    return list(dict.fromkeys(candidates))


def fetch_html(
    url: str = DEFAULT_URL,
    *,
    timeout: int = 30,
    start_date: date | None = None,
    end_date: date | None = None,
    user_agent: str | None = None,
    retries: int = 3,
) -> str:
    """Fetch the WA AG page. Date range is filtered downstream, not at fetch."""
    return fetch_url(
        _candidate_urls(url),
        timeout=timeout,
        retries=retries,
        user_agent=user_agent,
        headers={"Referer": "https://www.atg.wa.gov/"},
    )


def parse_breach_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    parser = _ATGBreachTableParser(base_url=base_url)
    parser.feed(html)

    for table in parser.tables:
        headers: list[str] = []
        records: list[dict[str, str]] = []

        for row_type, row in table:
            if row_type == "header" and not headers:
                headers = [cell.text for _, cell in row]
                continue

            if not headers and row_type == "data":
                headers = [f"Column {idx + 1}" for idx, _ in enumerate(row)]

            if not headers:
                continue

            values: dict[str, str] = {}
            for idx, (_, cell) in enumerate(row):
                header = headers[idx] if idx < len(headers) else f"Column {idx + 1}"
                key = _slugify(header)
                values[key] = cell.text
                if cell.href:
                    values[f"{key}_url"] = cell.href

            if any(values.values()):
                records.append(values)

        if records:
            return normalize_records(records)

    return []
