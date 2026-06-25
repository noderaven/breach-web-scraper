"""Maine Attorney General breach notification connector."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from breach_scraper.http import fetch_url

DEFAULT_URL = (
    "https://www.maine.gov/agviewer/content/ag/985235c7-cb95-4be2-8792-a1252b4f8318/list.html"
)
DATE_FIELDS = (
    "date_of_breach",
    "date_breach_discovered",
    "date_of_consumer_notification",
    "date_reported",
)
COUNT_FIELDS = ("total_persons_affected", "number_of_maine_residents_affected")
PREFERRED_FIELD_ORDER = (
    "date_of_breach",
    "date_reported",
    "organization_name",
    "organization_name_url",
    "organization_type",
    "number_of_maine_residents_affected",
    "total_persons_affected",
    "date_breach_discovered",
    "date_of_consumer_notification",
    "type_of_notification",
    "information_compromised",
    "breach_description",
    "notice",
    "notice_url",
)
LABEL_MAP = {
    "Type of Organization": "organization_type",
    "Entity Name": "organization_name",
    "Total number of persons affected (including residents)": "total_persons_affected",
    "Total number of Maine residents affected": "number_of_maine_residents_affected",
    "Date(s) Breach Occured": "date_of_breach",
    "Date Breach Discovered": "date_breach_discovered",
    "Description of the Breach": "breach_description",
    "Information Acquired - Name or other personal identifier in combination with": "information_compromised",  # noqa: E501
    "Type of Notification": "type_of_notification",
    "Date(s) of consumer notification": "date_of_consumer_notification",
    "Copy of notice to affected Maine residents": "notice",
}
DATE_TOKEN_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{1,2}-\d{1,2}-\d{4}"
    r"|[A-Za-z]+\s+\d{1,2},\s+\d{4}"
)
DETAIL_LINK_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.html$", re.IGNORECASE
)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_count(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""

    digits = re.sub(r"[^\d]", "", cleaned)
    if not digits:
        return cleaned
    return f"{int(digits):,}"


def _normalize_single_date(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""

    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return cleaned


def _repair_malformed_date_text(value: str) -> str:
    repaired = _clean_text(value)
    repaired = re.sub(
        r"\b(\d{1,2})/(\d{1,2})/2-(\d{2})\b",
        lambda match: f"{int(match.group(1)):02d}/{int(match.group(2)):02d}/20{match.group(3)}",
        repaired,
    )
    repaired = re.sub(
        r"\b(\d{2})(\d{2})(20\d{2})\b",
        lambda match: f"{match.group(1)}/{match.group(2)}/{match.group(3)}",
        repaired,
    )
    return repaired


def _normalize_dateish(value: str) -> str:
    cleaned = _repair_malformed_date_text(value)
    if not cleaned:
        return ""

    tokens = DATE_TOKEN_RE.findall(cleaned)
    if not tokens:
        return cleaned

    normalized_tokens = [_normalize_single_date(token) for token in tokens]
    if len(normalized_tokens) == 1 and cleaned == tokens[0]:
        return normalized_tokens[0]
    return " to ".join(normalized_tokens)


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
            cleaned = _normalize_dateish(cleaned)
        elif key in COUNT_FIELDS:
            cleaned = _normalize_count(cleaned)
        normalized[key] = cleaned

    if not normalized.get("date_reported"):
        normalized["date_reported"] = normalized.get(
            "date_of_consumer_notification"
        ) or normalized.get("date_breach_discovered", "")
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
        headers={"Referer": "https://www.maine.gov/"},
    )


@dataclass
class _ListLink:
    url: str
    text_chunks: list[str] = field(default_factory=list)
    date_reported: str = ""

    @property
    def text(self) -> str:
        return _clean_text(" ".join(self.text_chunks))


class _ListCell:
    def __init__(self) -> None:
        self.text_chunks: list[str] = []
        self.href: str | None = None

    def add_text(self, text: str) -> None:
        if text:
            self.text_chunks.append(text)

    @property
    def text(self) -> str:
        return _clean_text("".join(self.text_chunks))


class _MaineAGListParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_tag: str | None = None
        self.current_row: list[_ListCell] = []
        self.current_cell: _ListCell | None = None
        self.entries: list[_ListLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and "breachTable" in (attrs_dict.get("class") or ""):
            self.in_table = True
            return

        if not self.in_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
            return

        if self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.current_tag = tag
            self.current_cell = _ListCell()
            return

        if tag == "a" and self.in_cell and self.current_cell:
            href = attrs_dict.get("href")
            if not href:
                return
            absolute = urljoin(self.base_url, href)
            if absolute.endswith("list.html") or not DETAIL_LINK_RE.search(absolute):
                return
            self.current_cell.href = absolute

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell:
            self.current_cell.add_text(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_table:
            return

        if tag == "table":
            self.in_table = False
            return

        if tag in {"td", "th"} and self.in_cell and self.current_cell and self.current_tag == tag:
            self.current_row.append(self.current_cell)
            self.in_cell = False
            self.current_tag = None
            self.current_cell = None
            return

        if tag == "tr" and self.in_row:
            self.in_row = False
            if len(self.current_row) >= 2 and self.current_row[1].href:
                self.entries.append(
                    _ListLink(
                        url=self.current_row[1].href or "",
                        text_chunks=self.current_row[1].text_chunks,
                        date_reported=self.current_row[0].text,
                    )
                )


def _entry_in_date_range(entry: _ListLink, start_date: date | None, end_date: date | None) -> bool:
    if start_date is None or end_date is None:
        return True
    normalized = _normalize_dateish(entry.date_reported)
    match = re.match(r"^\d{4}-\d{2}-\d{2}", normalized)
    if not match:
        return False
    reported_date = date.fromisoformat(match.group(0))
    return start_date <= reported_date <= end_date


class _SimpleLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.current_link: _ListLink | None = None
        self.links: list[_ListLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        href = dict(attrs).get("href")
        if not href:
            return

        absolute = urljoin(self.base_url, href)
        if absolute.endswith("list.html") or not DETAIL_LINK_RE.search(absolute):
            return

        self.current_link = _ListLink(url=absolute)

    def handle_data(self, data: str) -> None:
        if self.current_link:
            self.current_link.text_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_link:
            self.links.append(self.current_link)
            self.current_link = None


def extract_detail_links(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    entries = extract_list_entries(html, base_url=base_url)
    if entries:
        return [{"url": entry["url"], "text": entry["text"]} for entry in entries]

    seen: set[str] = set()
    links: list[dict[str, str]] = []
    parser = _SimpleLinkParser(base_url=base_url)
    parser.feed(html)
    for link in parser.links:
        if link.url in seen:
            continue
        seen.add(link.url)
        links.append({"url": link.url, "text": link.text})
    return links


def extract_list_entries(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    parser = _MaineAGListParser(base_url=base_url)
    parser.feed(html)

    seen: set[str] = set()
    entries: list[dict[str, str]] = []
    for link in parser.entries:
        if link.url in seen:
            continue
        seen.add(link.url)
        entries.append({"url": link.url, "text": link.text, "date_reported": link.date_reported})
    return entries


@dataclass
class _DetailItem:
    text_chunks: list[str] = field(default_factory=list)
    href: str | None = None

    def add_child_text(self, text: str) -> None:
        if not text:
            return
        if self.text_chunks and self.text_chunks[-1].rstrip().endswith(":"):
            self.text_chunks.append(" " + text)
        else:
            self.text_chunks.append("; " + text)

    @property
    def text(self) -> str:
        return _clean_text(" ".join(self.text_chunks))


class _MaineAGDetailParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.current_section = ""
        self.in_heading = False
        self.heading_chunks: list[str] = []
        self.item_stack: list[_DetailItem] = []
        self.items: list[tuple[str, str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"h1", "h2", "h3", "h4"}:
            self.in_heading = True
            self.heading_chunks = []
            return

        if tag == "li":
            self.item_stack.append(_DetailItem())
            return

        if tag == "a" and self.item_stack:
            href = attrs_dict.get("href")
            if href and not self.item_stack[-1].href:
                self.item_stack[-1].href = urljoin(self.base_url, href)

    def handle_data(self, data: str) -> None:
        if self.in_heading:
            self.heading_chunks.append(data)
        elif self.item_stack:
            self.item_stack[-1].text_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4"} and self.in_heading:
            self.in_heading = False
            self.current_section = _clean_text(" ".join(self.heading_chunks))
            self.heading_chunks = []
            return

        if tag == "li" and self.item_stack:
            item = self.item_stack.pop()
            text = item.text
            if self.item_stack:
                self.item_stack[-1].add_child_text(text)
            elif text:
                self.items.append((self.current_section, text, item.href))


def _split_label_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        return _clean_text(text), ""
    label, value = text.split(":", 1)
    return _clean_text(label), _clean_text(value)


def parse_detail_page(html: str, page_url: str) -> dict[str, str]:
    parser = _MaineAGDetailParser(base_url=page_url)
    parser.feed(html)

    record: dict[str, str] = {"organization_name_url": page_url}
    for _section, text, href in parser.items:
        label, value = _split_label_value(text)
        key = LABEL_MAP.get(label)
        if not key:
            continue

        if key == "notice":
            record[key] = value or "View notice"
            if href:
                record["notice_url"] = href
        else:
            record[key] = value
            if href:
                record[f"{key}_url"] = href

    return record


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
    detail_links = extract_list_entries(list_html, base_url=url)
    if start_date is not None and end_date is not None:
        detail_links = [
            link
            for link in detail_links
            if _entry_in_date_range(
                _ListLink(
                    url=link["url"],
                    text_chunks=[link["text"]],
                    date_reported=link.get("date_reported", ""),
                ),
                start_date,
                end_date,
            )
        ]
    if limit > 0:
        detail_links = detail_links[:limit]
    detail_pages = [
        {
            "url": link["url"],
            "html": _fetch_url(
                link["url"], timeout=timeout, user_agent=user_agent, retries=retries
            ),
            "list_text": link["text"],
        }
        for link in detail_links
    ]
    return json.dumps({"list_url": url, "detail_pages": detail_pages})


def parse_breach_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, str]]:
    stripped = html.lstrip()
    if stripped.startswith("{"):
        payload = json.loads(stripped)
        detail_pages = payload.get("detail_pages", [])
        records = []
        for page in detail_pages:
            record = parse_detail_page(page.get("html", ""), page.get("url", base_url))
            if page.get("list_text") and not record.get("organization_name"):
                record["organization_name"] = page["list_text"]
            records.append(record)
        return normalize_records([record for record in records if any(record.values())])

    detail_links = extract_detail_links(html, base_url=base_url)
    if detail_links:
        records = []
        for link in detail_links:
            record = {
                "organization_name": link["text"] or "View detail",
                "organization_name_url": link["url"],
            }
            records.append(record)
        return normalize_records(records)

    record = parse_detail_page(html, page_url=base_url)
    if any(record.values()):
        return normalize_records([record])
    return []
