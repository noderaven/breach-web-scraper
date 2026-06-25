"""Shared output helpers for breach scraper connectors."""

from __future__ import annotations

import csv
import html
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _generated_timestamp() -> str:
    try:
        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %I:%M %p %Z")
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone().strftime("%Y-%m-%d %I:%M %p %Z")


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


def _parse_date_value(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _date_sort_key(value: Any) -> str:
    parsed = _parse_date_value(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%dT%H:%M:%S")
    return str(value or "").strip().casefold()


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


def _affected_count_numeric(row: Row) -> int | None:
    count_value = _html_affected_count(row).replace(",", "").strip()
    return int(count_value) if count_value.isdigit() else None


@dataclass(frozen=True)
class ReportStats:
    total_rows: int
    latest_report: str
    latest_report_sort_key: str
    known_count_rows: int
    known_affected: int
    missing_breach_date: int


def _report_stats(rows: Sequence[Row]) -> ReportStats:
    latest_report_display = ""
    latest_report_sort_key = ""
    known_affected = 0
    known_count_rows = 0
    missing_breach_date = 0

    for row in rows:
        numeric_count = _affected_count_numeric(row)
        if numeric_count is not None:
            known_affected += numeric_count
            known_count_rows += 1
        if not str(row.get("date_of_breach", "") or "").strip():
            missing_breach_date += 1

        reported_display = _html_reported_date(row)
        reported_sort_key = _date_sort_key(reported_display)
        if reported_display and (
            not latest_report_sort_key or reported_sort_key > latest_report_sort_key
        ):
            latest_report_display = reported_display
            latest_report_sort_key = reported_sort_key

    return ReportStats(
        total_rows=len(rows),
        latest_report=latest_report_display or "Unknown",
        latest_report_sort_key=latest_report_sort_key,
        known_count_rows=known_count_rows,
        known_affected=known_affected,
        missing_breach_date=missing_breach_date,
    )


def _report_summary(rows: Sequence[Row]) -> list[str]:
    stats = _report_stats(rows)
    return [
        f"- Total breach notices reviewed: {stats.total_rows}",
        f"- Latest reported date: {stats.latest_report}",
        f"- Records with known affected counts: {stats.known_count_rows}",
        f"- Total persons affected across known counts: {stats.known_affected:,}",
        f"- Records missing breach date: {stats.missing_breach_date}",
    ]


def _report_summary_items(rows: Sequence[Row]) -> list[tuple[str, str]]:
    stats = _report_stats(rows)
    return [
        ("Total breach notices reviewed", str(stats.total_rows)),
        ("Latest reported date", stats.latest_report),
        ("Records with known affected counts", str(stats.known_count_rows)),
        ("Total persons affected across known counts", f"{stats.known_affected:,}"),
        ("Records missing breach date", str(stats.missing_breach_date)),
    ]


def _html_source_metadata(
    row: Row,
    default_source_key: str | None,
    default_source_name: str | None,
) -> tuple[str, str]:
    source_key = str(
        row.get("_source_key", "") or row.get("source_key", "") or default_source_key or "unknown"
    )
    source_name = str(
        row.get("_source_name", "") or row.get("source", "") or default_source_name or source_key
    )
    return source_key, source_name


def _html_notice_link(row: Row) -> str:
    notice_url = str(row.get("notice_url", "") or row.get("organization_name_url", "") or "")
    if not notice_url:
        return ""
    return (
        f'<a href="{html.escape(notice_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        "View notice"
        "</a>"
    )


def _html_source_link(row: Row, source_name: str) -> str:
    source_url = str(row.get("_source_url", "") or "")
    safe_name = html.escape(source_name)
    if not source_url:
        return safe_name
    return (
        f'<a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f"{safe_name}"
        "</a>"
    )


def _html_organization_link(row: Row) -> str:
    organization_name = str(row.get("organization_name", "") or "Unknown organization")
    organization_url = str(row.get("organization_name_url", "") or "")
    safe_name = html.escape(organization_name)
    if not organization_url:
        return safe_name
    return (
        f'<a href="{html.escape(organization_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f"{safe_name}"
        "</a>"
    )


def _html_information_compromised(row: Row) -> str:
    detail_parts = [
        str(row.get("information_compromised", "") or ""),
        str(row.get("type_of_breach", "") or ""),
        str(row.get("location_of_breached_information", "") or ""),
        str(row.get("breach_description", "") or ""),
    ]
    compromised = " | ".join(part for part in detail_parts if part) or "Not listed"
    parts = [part.strip() for part in compromised.split(";") if part.strip()]
    if len(parts) <= 1:
        return html.escape(compromised)
    return "<br>".join(html.escape(part) for part in parts)


def _html_reported_date(row: Row) -> str:
    return str(
        row.get("date_reported", "")
        or row.get("date_of_consumer_notification", "")
        or row.get("date_breach_discovered", "")
        or ""
    )


def _html_affected_count(row: Row) -> str:
    return str(
        row.get("persons_affected", "")
        or row.get("total_persons_affected", "")
        or row.get("number_affected", "")
        or row.get("individuals_affected", "")
        or ""
    )


def to_html(
    records: Iterable[Row],
    title: str = "Breach Monitoring Report",
    source_url: str | None = None,
    *,
    default_source_key: str | None = None,
    default_source_name: str | None = None,
) -> str:
    rows = list(records)
    safe_title = html.escape(title)

    if not rows:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
</head>
<body>
  <main>
    <h1>{safe_title}</h1>
    <p>No records found.</p>
  </main>
</body>
</html>
"""

    source_buttons: list[str] = [
        '<button class="filter-button active" data-source-filter="all" type="button">All Sources</button>'
    ]
    seen_sources: list[tuple[str, str]] = []
    for row in rows:
        source_meta = _html_source_metadata(row, default_source_key, default_source_name)
        if source_meta not in seen_sources:
            seen_sources.append(source_meta)

    source_buttons.extend(
        (
            f'<button class="filter-button" data-source-filter="{html.escape(source_key, quote=True)}" type="button">'
            f"{html.escape(source_name)}"
            "</button>"
        )
        for source_key, source_name in seen_sources
    )

    source_markup = ""
    if source_url and len(seen_sources) == 1:
        host = urlparse(source_url).netloc or source_url
        source_markup = (
            '<p class="source">Source page: '
            f'<a href="{html.escape(source_url, quote=True)}">{html.escape(host)}</a>'
            "</p>"
        )

    summary_items = _report_summary_items(rows)
    summary_text = " | ".join(f"{label}: {value}" for label, value in summary_items)
    generated_at = _generated_timestamp()

    table_rows = "\n".join(
        (
            f"<tr "
            f'data-source-key="{html.escape(source_key, quote=True)}" '
            f'data-reported-display="{html.escape(reported_display or "Unknown", quote=True)}" '
            f'data-affected-count="{affected_numeric if affected_numeric is not None else ""}" '
            f'data-missing-breach-date="{1 if not breach_display else 0}" '
            f'data-sort-source="{html.escape(source_name.casefold(), quote=True)}" '
            f'data-sort-date_reported="{html.escape(_date_sort_key(reported_display), quote=True)}" '
            f'data-sort-organization="{html.escape(str(row.get("organization_name", "") or "").casefold(), quote=True)}" '
            f'data-sort-date_of_breach="{html.escape(_date_sort_key(breach_display), quote=True)}" '
            f'data-sort-affected="{affected_numeric if affected_numeric is not None else 0}" '
            f'data-sort-information="{html.escape(str(row.get("information_compromised", "") or "").casefold(), quote=True)}" '
            f'data-sort-notice="{html.escape(str(row.get("notice_url", "") or row.get("organization_name_url", "") or ""), quote=True)}"'
            f">"
            f'<td class="source-cell">{_html_source_link(row, source_name)}</td>'
            f"<td>{html.escape(reported_display or 'Unknown')}</td>"
            f'<td class="org-cell">{_html_organization_link(row)}</td>'
            f"<td>{html.escape(breach_display or 'Unknown')}</td>"
            f"<td>{html.escape(affected_display or 'Unknown')}</td>"
            f"<td>{_html_information_compromised(row)}</td>"
            f'<td class="notice-cell">{_html_notice_link(row) or "Unavailable"}</td>'
            "</tr>"
        )
        for row in rows
        for source_key, source_name in [
            _html_source_metadata(row, default_source_key, default_source_name)
        ]
        for reported_display in [_html_reported_date(row)]
        for breach_display in [str(row.get("date_of_breach", "") or "")]
        for affected_display in [_html_affected_count(row)]
        for affected_numeric in [_affected_count_numeric(row)]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f4ed;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #5b6470;
      --accent: #1d5f7a;
      --accent-soft: #d8e8ef;
      --line: #d9dee4;
      --shadow: 0 10px 24px rgba(31, 41, 51, 0.08);
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}

    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 28px 20px 48px;
    }}

    .hero {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 24px;
      margin-bottom: 20px;
    }}

    .eyebrow {{
      margin: 0 0 10px;
      font-size: 0.78rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(1.8rem, 3vw, 2.6rem);
      line-height: 1.1;
      font-weight: 700;
    }}

    .source {{
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}

    .updated-at {{
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}

    a {{
      color: var(--accent);
    }}

    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 16px 18px;
      margin-bottom: 16px;
    }}

    .filter-group {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .filter-button {{
      border: 1px solid var(--line);
      background: #f8fafb;
      color: var(--ink);
      border-radius: 999px;
      padding: 8px 14px;
      font: inherit;
      cursor: pointer;
    }}

    .filter-button.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }}

    .summary-line {{
      margin: 0;
      font-size: 0.95rem;
      color: var(--muted);
    }}

    .table-shell {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}

    .table-wrap {{
      overflow: auto;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }}

    thead th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #eef4f7;
      border-bottom: 1px solid var(--line);
      padding: 0;
    }}

    tbody td {{
      padding: 14px 16px;
      vertical-align: top;
      border-bottom: 1px solid var(--line);
      line-height: 1.45;
    }}

    tbody tr:nth-child(even) {{
      background: #fbfcfd;
    }}

    tbody tr.hidden-row {{
      display: none;
    }}

    .source-cell {{
      font-weight: 600;
      white-space: nowrap;
    }}

    .org-cell {{
      font-weight: 700;
    }}

    .notice-cell a {{
      white-space: nowrap;
    }}

    .sort-button {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      width: 100%;
      padding: 14px 16px;
      border: 0;
      background: transparent;
      color: var(--muted);
      font: inherit;
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      text-align: left;
      cursor: pointer;
    }}

    .sort-button:hover {{
      background: #e7f0f4;
      color: var(--ink);
    }}

    .sort-button.active {{
      color: var(--accent);
    }}

    .sort-indicator {{
      flex: 0 0 auto;
      font-size: 0.9rem;
      line-height: 1;
    }}

    .empty-state {{
      display: none;
      padding: 18px;
      color: var(--muted);
    }}

    .pager {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px;
      border-top: 1px solid var(--line);
      background: #fcfdfe;
    }}

    .pager-status {{
      margin: 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}

    .pager-controls {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }}

    .page-size-label {{
      color: var(--muted);
      font-size: 0.95rem;
    }}

    .page-size-select {{
      border: 1px solid var(--line);
      background: #f8fafb;
      color: var(--ink);
      border-radius: 999px;
      padding: 8px 12px;
      font: inherit;
    }}

    .pager-button {{
      border: 1px solid var(--line);
      background: #f8fafb;
      color: var(--ink);
      border-radius: 999px;
      padding: 8px 14px;
      font: inherit;
      cursor: pointer;
    }}

    .pager-button[disabled] {{
      cursor: not-allowed;
      opacity: 0.45;
    }}

    @media print {{
      body {{
        background: white;
      }}

      .hero,
      .toolbar,
      .table-shell {{
        box-shadow: none;
      }}

      main {{
        max-width: none;
        padding: 0;
      }}

      .filter-group {{
        display: none;
      }}
    }}

    @media (max-width: 720px) {{
      main {{
        padding: 18px 12px 32px;
      }}

      .hero {{
        padding: 18px;
      }}

      .toolbar {{
        padding: 14px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <p class="eyebrow">Breach Monitoring</p>
      <h1>{safe_title}</h1>
      {source_markup}
      <p class="updated-at">Last updated: {html.escape(generated_at)}</p>
    </section>

    <section class="toolbar">
      <div class="filter-group" aria-label="Source filters">
        {" ".join(source_buttons)}
      </div>
      <p class="summary-line" id="summary-line">{html.escape(summary_text)}</p>
    </section>

    <section class="table-shell">
      <div class="table-wrap">
        <table aria-label="Breach notices table">
          <thead>
            <tr>
              <th aria-sort="none"><button class="sort-button" data-sort-key="source" type="button"><span>Source</span><span class="sort-indicator">&#8597;</span></button></th>
              <th aria-sort="descending"><button class="sort-button active" data-sort-key="date_reported" type="button"><span>Reported / Notified</span><span class="sort-indicator">&#8595;</span></button></th>
              <th aria-sort="none"><button class="sort-button" data-sort-key="organization" type="button"><span>Organization</span><span class="sort-indicator">&#8597;</span></button></th>
              <th aria-sort="none"><button class="sort-button" data-sort-key="date_of_breach" type="button"><span>Date of Breach</span><span class="sort-indicator">&#8597;</span></button></th>
              <th aria-sort="none"><button class="sort-button" data-sort-key="affected" type="button"><span>Persons Affected</span><span class="sort-indicator">&#8597;</span></button></th>
              <th aria-sort="none"><button class="sort-button" data-sort-key="information" type="button"><span>Details</span><span class="sort-indicator">&#8597;</span></button></th>
              <th aria-sort="none"><button class="sort-button" data-sort-key="notice" type="button"><span>Notice</span><span class="sort-indicator">&#8597;</span></button></th>
            </tr>
          </thead>
          <tbody id="breach-table-body">
            {table_rows}
          </tbody>
        </table>
      </div>
      <div class="empty-state" id="empty-state">No records match the selected source filter.</div>
      <div class="pager" id="pager">
        <p class="pager-status" id="pager-status">Page 1 of 1</p>
        <div class="pager-controls">
          <label class="page-size-label" for="page-size-select">Items per page</label>
          <select class="page-size-select" id="page-size-select">
            <option value="20">20</option>
            <option value="50" selected>50</option>
            <option value="100">100</option>
          </select>
          <button class="pager-button" id="pager-first" type="button">First</button>
          <button class="pager-button" id="pager-prev" type="button">Previous</button>
          <button class="pager-button" id="pager-next" type="button">Next</button>
          <button class="pager-button" id="pager-last" type="button">Last</button>
        </div>
      </div>
    </section>
  </main>
  <script>
    (function () {{
      const buttons = Array.from(document.querySelectorAll("[data-source-filter]"));
      const rows = Array.from(document.querySelectorAll("tbody tr[data-source-key]"));
      const sortButtons = Array.from(document.querySelectorAll("[data-sort-key]"));
      const tableBody = document.getElementById("breach-table-body");
      const emptyState = document.getElementById("empty-state");
      const summaryLine = document.getElementById("summary-line");
      const pager = document.getElementById("pager");
      const pagerStatus = document.getElementById("pager-status");
      const pagerFirst = document.getElementById("pager-first");
      const pagerPrev = document.getElementById("pager-prev");
      const pagerNext = document.getElementById("pager-next");
      const pagerLast = document.getElementById("pager-last");
      const pageSizeSelect = document.getElementById("page-size-select");
      let pageSize = Number.parseInt(pageSizeSelect.value || "50", 10) || 50;
      let currentFilter = "all";
      let currentSortKey = "date_reported";
      let currentSortDirection = "desc";
      let currentPage = 1;

      function sortValue(row, sortKey) {{
        const value = row.dataset["sort" + sortKey.charAt(0).toUpperCase() + sortKey.slice(1)] || "";
        if (sortKey === "affected") {{
          return Number.parseInt(value || "0", 10) || 0;
        }}
        return value;
      }}

      function updateSortUi() {{
        sortButtons.forEach((button) => {{
          const isActive = button.dataset.sortKey === currentSortKey;
          button.classList.toggle("active", isActive);
          const indicator = button.querySelector(".sort-indicator");
          const th = button.closest("th");
          if (!isActive) {{
            indicator.textContent = String.fromCharCode(8597);
            th.setAttribute("aria-sort", "none");
            return;
          }}
          indicator.textContent = String.fromCharCode(currentSortDirection === "asc" ? 8593 : 8595);
          th.setAttribute("aria-sort", currentSortDirection === "asc" ? "ascending" : "descending");
        }});
      }}

      function applySort() {{
        const direction = currentSortDirection === "asc" ? 1 : -1;
        rows
          .slice()
          .sort((left, right) => {{
            const leftValue = sortValue(left, currentSortKey);
            const rightValue = sortValue(right, currentSortKey);
            if (leftValue < rightValue) {{
              return -1 * direction;
            }}
            if (leftValue > rightValue) {{
              return 1 * direction;
            }}

            const leftOrg = left.dataset.sortOrganization || "";
            const rightOrg = right.dataset.sortOrganization || "";
            if (leftOrg < rightOrg) {{
              return -1;
            }}
            if (leftOrg > rightOrg) {{
              return 1;
            }}
            return 0;
          }})
          .forEach((row) => tableBody.appendChild(row));
        updateSortUi();
      }}

      function filteredRows() {{
        return rows.filter((row) => currentFilter === "all" || row.dataset.sourceKey === currentFilter);
      }}

      function formatNumber(value) {{
        return new Intl.NumberFormat("en-US").format(value);
      }}

      function updateSummaryLine(visibleRows) {{
        const totalRows = visibleRows.length;
        let latestSortKey = "";
        let latestDisplay = "Unknown";
        let knownCountRows = 0;
        let totalAffected = 0;
        let missingBreachDate = 0;

        visibleRows.forEach((row) => {{
          const affectedCount = Number.parseInt(row.dataset.affectedCount || "", 10);
          if (Number.isFinite(affectedCount)) {{
            knownCountRows += 1;
            totalAffected += affectedCount;
          }}

          if ((row.dataset.missingBreachDate || "0") === "1") {{
            missingBreachDate += 1;
          }}

          const sortKey = row.dataset.sortDate_reported || "";
          if (sortKey && (!latestSortKey || sortKey > latestSortKey)) {{
            latestSortKey = sortKey;
            latestDisplay = row.dataset.reportedDisplay || "Unknown";
          }}
        }});

        summaryLine.textContent = [
          "Total breach notices reviewed: " + totalRows,
          "Latest reported date: " + latestDisplay,
          "Records with known affected counts: " + knownCountRows,
          "Total persons affected across known counts: " + formatNumber(totalAffected),
          "Records missing breach date: " + missingBreachDate,
        ].join(" | ");
      }}

      function applyPagination() {{
        const visibleRows = filteredRows();
        const totalRows = visibleRows.length;
        const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
        currentPage = Math.min(currentPage, totalPages);

        const startIndex = (currentPage - 1) * pageSize;
        const endIndex = startIndex + pageSize;

        rows.forEach((row) => row.classList.add("hidden-row"));
        visibleRows.forEach((row, index) => {{
          row.classList.toggle("hidden-row", index < startIndex || index >= endIndex);
        }});

        updateSummaryLine(visibleRows);
        emptyState.style.display = totalRows === 0 ? "block" : "none";
        pager.style.display = totalRows === 0 ? "none" : "flex";
        pagerStatus.textContent = totalRows === 0
          ? "No matching records"
          : "Page " + currentPage + " of " + totalPages + " | Showing " + (startIndex + 1) + "-" + Math.min(endIndex, totalRows) + " of " + totalRows + " records";
        pagerFirst.disabled = currentPage === 1 || totalRows === 0;
        pagerPrev.disabled = currentPage === 1 || totalRows === 0;
        pagerNext.disabled = currentPage === totalPages || totalRows === 0;
        pagerLast.disabled = currentPage === totalPages || totalRows === 0;
      }}

      function applyFilter(sourceKey) {{
        currentFilter = sourceKey;
        currentPage = 1;
        buttons.forEach((button) => {{
          button.classList.toggle("active", button.dataset.sourceFilter === sourceKey);
        }});
        applyPagination();
      }}

      buttons.forEach((button) => {{
        button.addEventListener("click", () => applyFilter(button.dataset.sourceFilter));
      }});

      sortButtons.forEach((button) => {{
        button.addEventListener("click", () => {{
          const nextSortKey = button.dataset.sortKey;
          if (currentSortKey === nextSortKey) {{
            currentSortDirection = currentSortDirection === "asc" ? "desc" : "asc";
          }} else {{
            currentSortKey = nextSortKey;
            currentSortDirection = nextSortKey === "organization" || nextSortKey === "source" ? "asc" : "desc";
          }}
          currentPage = 1;
          applySort();
          applyPagination();
        }});
      }});

      pagerFirst.addEventListener("click", () => {{
        currentPage = 1;
        applyPagination();
      }});

      pagerPrev.addEventListener("click", () => {{
        currentPage = Math.max(1, currentPage - 1);
        applyPagination();
      }});

      pagerNext.addEventListener("click", () => {{
        const totalPages = Math.max(1, Math.ceil(filteredRows().length / pageSize));
        currentPage = Math.min(totalPages, currentPage + 1);
        applyPagination();
      }});

      pagerLast.addEventListener("click", () => {{
        currentPage = Math.max(1, Math.ceil(filteredRows().length / pageSize));
        applyPagination();
      }});

      pageSizeSelect.addEventListener("change", () => {{
        pageSize = Number.parseInt(pageSizeSelect.value || "50", 10) || 50;
        currentPage = 1;
        applyPagination();
      }});

      applySort();
      applyFilter(currentFilter);
    }})();
  </script>
</body>
</html>
"""


def to_report(
    records: Iterable[Row], title: str = "Breach Monitoring Report", source_url: str | None = None
) -> str:
    rows = list(records)
    if not rows:
        return f"# {title}\n\nNo records found."

    lines = [f"# {title}", ""]
    if source_url:
        host = urlparse(source_url).netloc or source_url
        lines.append(f"Source: [{host}]({source_url})")
        lines.append("")

    lines.append("## Summary")
    lines.extend(_report_summary(rows))
    lines.append("")
    lines.append("## Breach Notices")
    lines.append("")

    for idx, row in enumerate(rows, start=1):
        name = str(row.get("organization_name", "") or "Unknown organization")
        lines.append(f"### {idx}. {name}")
        lines.append(f"- Date reported: {row.get('date_reported', '') or 'Unknown'}")
        lines.append(f"- Date of breach: {row.get('date_of_breach', '') or 'Unknown'}")
        lines.append(
            "- Persons affected: "
            f"{row.get('persons_affected', '') or row.get('total_persons_affected', '') or row.get('number_affected', '') or row.get('individuals_affected', '') or 'Unknown'}"
        )
        lines.append(
            f"- Information compromised: {row.get('information_compromised', '') or 'Not listed'}"
        )
        if row.get("organization_name_url"):
            lines.append(f"- Notice letter: {row['organization_name_url']}")
        elif row.get("notice_url"):
            lines.append(f"- Notice letter: {row['notice_url']}")
        lines.append("")

    return "\n".join(lines).strip()


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


def write_output(
    records: Sequence[Row],
    output_format: str,
    out_file: str | None,
    *,
    title: str = "Breach Monitoring Report",
    source_url: str | None = None,
    source_key: str | None = None,
    source_name: str | None = None,
) -> None:
    public_records = _public_records(records)

    if output_format == "json":
        payload = json.dumps(public_records, indent=2)
    elif output_format == "markdown":
        payload = to_markdown(public_records)
    elif output_format == "html":
        payload = to_html(
            records,
            title=title,
            source_url=source_url,
            default_source_key=source_key,
            default_source_name=source_name,
        )
    elif output_format == "report":
        payload = to_report(public_records, title=title, source_url=source_url)
    elif output_format == "csv":
        if not public_records:
            payload = ""
        else:
            columns: list[str] = []
            for row in public_records:
                for key in row:
                    if key not in columns:
                        columns.append(key)

            buffer = StringIO()
            writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(public_records)
            payload = buffer.getvalue()
    else:
        raise ValueError(f"Unsupported output format: {output_format}")

    if out_file:
        Path(out_file).write_text(payload, encoding="utf-8")
    else:
        print(payload)
