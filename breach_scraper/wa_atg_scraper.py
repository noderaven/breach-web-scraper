"""Scraper for Washington Attorney General breach notifications.

This module fetches the WA AG data breach page, extracts the breach table,
normalizes the records, and can emit JSON/CSV/Markdown output. It also supports
an offline mode that parses a previously saved copy of the page.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_URL = "https://www.atg.wa.gov/data-breach-notifications"

# The WA AG site returns HTTP 403 for clients that do not look like a browser,
# so a browser-like User-Agent is the default; override it with --user-agent.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


def fetch_html(
    url: str = DEFAULT_URL,
    *,
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 0.5,
    user_agent: str | None = None,
) -> str:
    """Fetch page HTML, retrying transient errors with exponential backoff."""
    request = Request(
        url,
        headers={
            "User-Agent": user_agent or DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            # urlopen here targets a vetted HTTP(S) URL, which is the intended use.
            with urlopen(request, timeout=timeout) as response:  # nosec B310
                charset = response.headers.get_content_charset() or "utf-8"
                body: bytes = response.read()
                return body.decode(charset, errors="replace")
        except HTTPError as exc:
            if exc.code == 403:
                raise RuntimeError(
                    "Request blocked with HTTP 403. This source may require "
                    "browser-like access from your network. Try: (1) run with "
                    "--input-html using a saved copy of the page, (2) run from a "
                    "different network, or (3) pass a different --user-agent."
                ) from exc
            if 500 <= exc.code < 600:
                last_error = exc
            else:
                raise RuntimeError(f"Failed to fetch source page: HTTP {exc.code}.") from exc
        except (URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(backoff * (2**attempt))
    raise RuntimeError(
        f"Failed to fetch source page after {attempts} attempt(s): {last_error}"
    ) from last_error


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
    arg_parser.add_argument(
        "--input-html",
        help="Path to a previously saved HTML page (offline mode; --url is ignored).",
    )
    arg_parser.add_argument("--user-agent", help="Override the request User-Agent header.")
    arg_parser.add_argument(
        "--retries", type=int, default=3, help="Max fetch attempts on transient errors."
    )
    arg_parser.add_argument("--output", choices=["json", "csv", "markdown"], default="json")
    arg_parser.add_argument("--out-file", help="Optional output file path.")
    arg_parser.add_argument("--limit", type=int, default=0, help="Optional max records to output.")
    args = arg_parser.parse_args(argv)

    try:
        if args.input_html:
            html = Path(args.input_html).read_text(encoding="utf-8")
        else:
            html = fetch_html(args.url, retries=args.retries, user_agent=args.user_agent)
        records = parse_breach_table(html, base_url=args.url)

        if args.limit and args.limit > 0:
            records = records[: args.limit]

        write_output(records, output_format=args.output, out_file=args.out_file)
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
