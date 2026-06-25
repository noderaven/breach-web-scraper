"""Output formatting for breach records (json, csv, markdown)."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from io import StringIO
from pathlib import Path
from typing import Any

Row = Mapping[str, Any]

PREFERRED_DISPLAY_COLUMNS = (
    "source",
    "date_reported",
    "organization_name",
    "date_of_breach",
    "persons_affected",
    "total_persons_affected",
    "number_affected",
    "individuals_affected",
    "number_of_washingtonians_affected",
    "number_of_maine_residents_affected",
    "information_compromised",
    "type_of_breach",
    "breach_description",
    "location_of_breached_information",
    "notice",
)


def _humanize_column(name: str) -> str:
    return name.replace("_", " ").title()


def _markdown_escape(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
    text = text.replace("[", "\\[").replace("]", "\\]")
    return text


def _markdown_link_target(url: Any) -> str:
    return str(url or "").replace("\\", "%5C").replace(")", "%29").strip()


def _display_columns(rows: list[Row]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key.startswith("_"):
                continue
            if key.endswith("_url") and key[:-4] in row:
                continue
            if key not in seen:
                seen.append(key)
    ordered = [column for column in PREFERRED_DISPLAY_COLUMNS if column in seen]
    ordered.extend(column for column in seen if column not in ordered)
    return ordered


def _display_value(row: Row, column: str) -> str:
    value = row.get(column, "")
    link = row.get(f"{column}_url")
    safe_value = _markdown_escape(value)
    if link:
        label = safe_value or "View notice"
        return f"[{label}]({_markdown_link_target(link)})"
    return safe_value


def _public_record(row: Row) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _public_records(records: Iterable[Row]) -> list[dict[str, Any]]:
    return [_public_record(row) for row in records]


def to_markdown(records: Iterable[Row]) -> str:
    rows = list(records)
    if not rows:
        return "No records found."
    columns = _display_columns(rows)
    header = "| " + " | ".join(_humanize_column(column) for column in columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = [
        "| " + " | ".join(_display_value(row, column) for column in columns) + " |" for row in rows
    ]
    return "\n".join([header, divider, *body])


def _to_csv(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    columns: list[str] = []
    for row in records:
        for key in row:
            if key not in columns:
                columns.append(key)
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue()


def write_output(
    records: Sequence[Row],
    output_format: str,
    out_file: str | None,
    *,
    title: str = "Breach Monitoring Report",
) -> None:
    public_records = _public_records(records)

    if output_format == "json":
        payload = json.dumps(public_records, indent=2)
    elif output_format == "markdown":
        payload = to_markdown(public_records)
    elif output_format == "csv":
        payload = _to_csv(public_records)
    else:
        raise ValueError(f"Unsupported output format: {output_format}")

    if out_file:
        Path(out_file).write_text(payload, encoding="utf-8")
    else:
        print(payload)
