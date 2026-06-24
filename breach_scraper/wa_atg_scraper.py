"""Scraper for Washington Attorney General breach notifications.

This module fetches the WA AG data breach page, extracts the breach table,
normalizes the records, and can emit JSON/CSV/Markdown output.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_URL = "https://www.atg.wa.gov/data-breach-notifications"


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _slugify(header: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    return normalized or "column"


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

        if tag in {"th", "td"} and self.in_cell and self.current_cell and self.current_cell_tag == tag:
            self.current_row.append((tag, self.current_cell))
            self.in_cell = False
            self.current_cell_tag = None
            self.current_cell = None
            return

        if tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                row_type = "header" if all(cell_tag == "th" for cell_tag, _ in self.current_row) else "data"
                self.current_table.append((row_type, self.current_row))

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell:
            self.current_cell.add_text(data)


def fetch_html(url: str = DEFAULT_URL, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; breach-web-scraper/1.0; +https://example.com)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - expected for HTTP fetch
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(content_type, errors="replace")


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
            return records

    return []


def to_markdown(records: Iterable[dict[str, str]]) -> str:
    rows = list(records)
    if not rows:
        return "No records found."

    columns = list(rows[0].keys())
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = ["| " + " | ".join(row.get(column, "") for column in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def write_output(records: list[dict[str, str]], output_format: str, out_file: str | None) -> None:
    if output_format == "json":
        payload = json.dumps(records, indent=2)
    elif output_format == "markdown":
        payload = to_markdown(records)
    elif output_format == "csv":
        if not records:
            payload = ""
        else:
            columns = list(records[0].keys())
            if out_file:
                with open(out_file, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(records)
                return
            output = [",".join(columns)]
            for row in records:
                output.append(",".join(json.dumps(row.get(col, ""))[1:-1] for col in columns))
            payload = "\n".join(output)
    else:
        raise ValueError(f"Unsupported output format: {output_format}")

    if out_file:
        Path(out_file).write_text(payload, encoding="utf-8")
    else:
        print(payload)


def main(argv: list[str] | None = None) -> int:
    arg_parser = argparse.ArgumentParser(description="Scrape WA ATG breach notifications table.")
    arg_parser.add_argument("--url", default=DEFAULT_URL, help="Source page URL.")
    arg_parser.add_argument("--output", choices=["json", "csv", "markdown"], default="json")
    arg_parser.add_argument("--out-file", help="Optional output file path.")
    arg_parser.add_argument("--limit", type=int, default=0, help="Optional max records to output.")
    args = arg_parser.parse_args(argv)

    html = fetch_html(args.url)
    records = parse_breach_table(html, base_url=args.url)

    if args.limit and args.limit > 0:
        records = records[: args.limit]

    write_output(records, output_format=args.output, out_file=args.out_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
