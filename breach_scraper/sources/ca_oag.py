"""California Attorney General breach notification connector."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from breach_scraper.http import fetch_url

DEFAULT_URL = "https://oag.ca.gov/privacy/databreach/list"
DATE_FIELDS = ("date_of_breach", "date_reported")
DETAIL_URL_RE = re.compile(r"/ecrime/databreach/reports/")
PREFERRED_FIELD_ORDER = (
    "date_of_breach",
    "date_reported",
    "organization_name",
    "organization_name_url",
    "notice",
    "notice_url",
)
DATE_TOKEN_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|[A-Za-z]+\s+\d{1,2},\s+\d{4}"
)
ROW_VALUE_TOKEN_RE = re.compile(
    r"n/a"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|[A-Za-z]+\s+\d{1,2},\s+\d{4}",
    re.IGNORECASE,
)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_single_date(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return cleaned


def _normalize_dateish(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    if cleaned.lower() == "n/a":
        return "n/a"

    tokens = DATE_TOKEN_RE.findall(cleaned)
    if not tokens:
        return cleaned

    normalized = [_normalize_single_date(token) for token in tokens]
    if len(normalized) == 1 and cleaned == tokens[0]:
        return normalized[0]
    return ", ".join(normalized)


def _field_sort_key(item: tuple[str, str]) -> tuple[int, int | str]:
    key, _ = item
    if key in PREFERRED_FIELD_ORDER:
        return (0, PREFERRED_FIELD_ORDER.index(key))
    return (1, key)


def _record_sort_key(record: dict[str, str]) -> tuple[int, str, str, str]:
    date_of_breach = record.get("date_of_breach", "")
    date_reported = record.get("date_reported", "")
    primary_date = date_of_breach if date_of_breach and date_of_breach != "n/a" else date_reported
    return (
        1 if primary_date else 0,
        primary_date,
        date_reported,
        record.get("organization_name", ""),
    )


def _record_reported_date(record: dict[str, str]) -> date | None:
    normalized = normalize_record(record)
    value = normalized.get("date_reported", "")
    match = re.match(r"^\d{4}-\d{2}-\d{2}", value)
    if match:
        return date.fromisoformat(match.group(0))
    return None


def _record_in_date_range(
    record: dict[str, str], start_date: date | None, end_date: date | None
) -> bool:
    if start_date is None or end_date is None:
        return True
    reported_date = _record_reported_date(record)
    if reported_date is None:
        return False
    return start_date <= reported_date <= end_date


def normalize_record(record: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in record.items():
        cleaned = _clean_text(value)
        if key in DATE_FIELDS:
            cleaned = _normalize_dateish(cleaned)
        normalized[key] = cleaned
    return dict(sorted(normalized.items(), key=_field_sort_key))


def normalize_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized = [normalize_record(record) for record in records]
    return sorted(normalized, key=_record_sort_key, reverse=True)


def _fetch_url(
    url: str, *, timeout: int = 30, user_agent: str | None = None, retries: int = 3
) -> str:
    return fetch_url(
        [url],
        timeout=timeout,
        retries=retries,
        user_agent=user_agent,
        headers={"Referer": "https://oag.ca.gov/"},
    )


@dataclass
class _Link:
    href: str
    text_chunks: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _clean_text(" ".join(self.text_chunks))


@dataclass
class _Block:
    text_chunks: list[str] = field(default_factory=list)
    links: list[_Link] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _clean_text(" ".join(self.text_chunks))


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


class _ListTableParser(HTMLParser):
    """Extract the first table matching the California breach list columns."""

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
                row_type = (
                    "header"
                    if all(cell_tag == "th" for cell_tag, _ in self.current_row)
                    else "data"
                )
                self.current_table.append((row_type, self.current_row))

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell:
            self.current_cell.add_text(data)


def _map_header(header: str) -> str:
    cleaned = _clean_text(header).lower()
    if "organization" in cleaned:
        return "organization_name"
    if "date(s) of breach" in cleaned or "date of breach" in cleaned:
        return "date_of_breach"
    if "reported date" in cleaned:
        return "date_reported"
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_") or "column"


def _parse_list_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    parser = _ListTableParser(base_url=base_url)
    parser.feed(html)

    for table in parser.tables:
        headers: list[str] = []
        records: list[dict[str, str]] = []
        for row_type, row in table:
            if row_type == "header" and not headers:
                headers = [_map_header(cell.text) for _, cell in row]
                continue

            if not headers:
                continue

            values: dict[str, str] = {}
            for idx, (_, cell) in enumerate(row):
                if idx >= len(headers):
                    continue
                key = headers[idx]
                values[key] = cell.text
                if cell.href:
                    values[f"{key}_url"] = cell.href

            if values.get("organization_name") and values.get("date_reported") is not None:
                records.append(values)

        if records and {"organization_name", "date_of_breach", "date_reported"}.issubset(
            records[0].keys()
        ):
            return records

    return []


class _ListRowParser(HTMLParser):
    BLOCK_TAGS = {"tr", "li", "div", "article", "section"}

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.blocks: list[_Block] = []
        self.candidates: list[_Block] = []
        self.current_link: _Link | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in self.BLOCK_TAGS:
            self.blocks.append(_Block())
            return

        if tag == "a":
            href = attrs_dict.get("href")
            if href:
                absolute = urljoin(self.base_url, href)
                if DETAIL_URL_RE.search(absolute):
                    self.current_link = _Link(href=absolute)

    def handle_data(self, data: str) -> None:
        if self.current_link:
            self.current_link.text_chunks.append(data)
        if self.blocks:
            for block in self.blocks:
                block.text_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_link:
            for block in self.blocks:
                block.links.append(self.current_link)
            self.current_link = None
            return

        if tag in self.BLOCK_TAGS and self.blocks:
            block = self.blocks.pop()
            if block.links:
                self.candidates.append(block)


def _candidate_to_record(block: _Block) -> dict[str, str] | None:
    if not block.links:
        return None

    link = block.links[0]
    organization_name = link.text
    if not organization_name:
        return None

    text = block.text
    if organization_name in text:
        remainder = _clean_text(text.split(organization_name, 1)[1])
    else:
        remainder = text

    if not remainder:
        return None

    tokens = [match.group(0) for match in ROW_VALUE_TOKEN_RE.finditer(remainder)]
    if not tokens:
        return None

    reported_date = tokens[-1]
    reported_index = remainder.rfind(reported_date)
    breach_part = _clean_text(remainder[:reported_index])
    if not breach_part:
        breach_part = tokens[0] if len(tokens) == 1 else ", ".join(tokens[:-1])

    return {
        "organization_name": organization_name,
        "organization_name_url": link.href,
        "date_of_breach": breach_part or "n/a",
        "date_reported": reported_date,
    }


def _parse_list_rows(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    parser = _ListRowParser(base_url=base_url)
    parser.feed(html)

    best_by_url: dict[str, tuple[int, dict[str, str]]] = {}
    for block in parser.candidates:
        record = _candidate_to_record(block)
        if not record:
            continue

        text_length = len(block.text)
        existing = best_by_url.get(record["organization_name_url"])
        if existing is None or text_length < existing[0]:
            best_by_url[record["organization_name_url"]] = (text_length, record)

    return [item[1] for item in best_by_url.values()]


def parse_list_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    records = _parse_list_table(html, base_url=base_url)
    if records:
        return records
    return _parse_list_rows(html, base_url=base_url)


class _DetailPdfParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.pdf_links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.current_href = urljoin(self.base_url, href)
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_href is not None:
            text = _clean_text(" ".join(self.current_text))
            self.pdf_links.append((self.current_href, text))
            self.current_href = None
            self.current_text = []


def parse_detail_page(html: str, page_url: str) -> dict[str, str]:
    parser = _DetailPdfParser(base_url=page_url)
    parser.feed(html)

    for href, text in parser.pdf_links:
        if href.lower().endswith(".pdf"):
            return {
                "organization_name_url": page_url,
                "notice": text or "Sample Notification",
                "notice_url": href,
            }

    return {"organization_name_url": page_url}


def fetch_html(
    url: str = DEFAULT_URL,
    *,
    timeout: int = 30,
    limit: int = 0,
    start_date: date | None = None,
    end_date: date | None = None,
    user_agent: str | None = None,
    retries: int = 3,
) -> str:
    list_html = _fetch_url(url, timeout=timeout, user_agent=user_agent, retries=retries)
    records = parse_list_table(list_html, base_url=url)
    if start_date is not None and end_date is not None:
        records = [
            record for record in records if _record_in_date_range(record, start_date, end_date)
        ]
    if limit > 0:
        records = records[:limit]

    detail_pages = []
    for record in records:
        detail_url = record.get("organization_name_url")
        if not detail_url:
            continue
        detail_pages.append(
            {
                "url": detail_url,
                "html": _fetch_url(
                    detail_url, timeout=timeout, user_agent=user_agent, retries=retries
                ),
            }
        )

    return json.dumps({"list_url": url, "records": records, "detail_pages": detail_pages})


def parse_breach_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    stripped = html.lstrip()
    if stripped.startswith("{"):
        payload = json.loads(stripped)
        records = payload.get("records")
        if records is None:
            list_html = payload.get("list_html", "")
            records = parse_list_table(list_html, base_url=payload.get("list_url", base_url))
        details_by_url = {}
        for page in payload.get("detail_pages", []):
            page_url = page.get("url", "")
            if page_url:
                details_by_url[page_url] = parse_detail_page(page.get("html", ""), page_url)

        merged = []
        for record in records:
            detail = details_by_url.get(record.get("organization_name_url", ""), {})
            merged_record = dict(record)
            merged_record.update({key: value for key, value in detail.items() if value})
            merged.append(merged_record)
        return normalize_records(merged)

    records = parse_list_table(html, base_url=base_url)
    if records:
        return normalize_records(records)

    detail = parse_detail_page(html, page_url=base_url)
    if any(detail.values()):
        return normalize_records([detail])
    return []
