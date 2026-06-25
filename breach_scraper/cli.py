"""Shared CLI for breach scraper connectors."""

from __future__ import annotations

import argparse
import calendar
import logging
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from breach_scraper.output import write_output
from breach_scraper.registry import SourceDefinition, list_sources

DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DEFAULT_MONTH_WINDOW = 6

Row = Mapping[str, Any]
LOGGER = logging.getLogger(__name__)


class SourceRunError(RuntimeError):
    """Raised when one or more sources fail in strict mode."""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape and combine breach notification data from supported sources."
    )
    parser.add_argument("--output", choices=["json", "csv", "markdown"], default="json")
    parser.add_argument("--out-file", help="Optional output file path.")
    parser.add_argument(
        "--start-date",
        help="Inclusive start date (YYYY-MM-DD). Defaults to six months before --end-date.",
    )
    parser.add_argument(
        "--end-date",
        help="Inclusive end date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        metavar="KEY",
        help="Limit scraping to a source key. Repeat to include multiple sources.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List supported source keys and exit.",
    )
    parser.add_argument(
        "--input-html",
        help="Parse a previously saved HTML page (offline). Requires exactly one --source.",
    )
    parser.add_argument("--user-agent", help="Override the request User-Agent header.")
    parser.add_argument(
        "--retries", type=int, default=3, help="Max fetch attempts per source on transient errors."
    )
    parser.add_argument(
        "--include-undated",
        action="store_true",
        help="Keep records with no parseable relevant date instead of filtering them out.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail the run if any source cannot be fetched or parsed.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show per-source progress information on stderr.",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )


def _parse_iso_date(value: str, option_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{option_name} must be in YYYY-MM-DD format.") from exc


def _safe_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _subtract_months(value: date, months: int) -> date:
    year = value.year
    month = value.month - months
    while month <= 0:
        year -= 1
        month += 12
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def resolve_date_range(
    start_date_arg: str | None = None,
    end_date_arg: str | None = None,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    end_date = (
        _parse_iso_date(end_date_arg, "--end-date") if end_date_arg else (today or date.today())
    )
    start_date = (
        _parse_iso_date(start_date_arg, "--start-date")
        if start_date_arg
        else _subtract_months(end_date, DEFAULT_MONTH_WINDOW)
    )
    if start_date > end_date:
        raise ValueError("--start-date cannot be after --end-date.")
    return start_date, end_date


def _record_relevant_date(record: Row) -> date | None:
    date_reported = str(record.get("date_reported", "") or "")
    match = DATE_PREFIX_RE.match(date_reported)
    if match:
        parsed = _safe_iso_date(match.group(0))
        if parsed is not None:
            return parsed

    candidates: list[date] = []
    for token in ISO_DATE_RE.findall(str(record.get("date_of_breach", "") or "")):
        parsed = _safe_iso_date(token)
        if parsed is not None:
            candidates.append(parsed)
    return max(candidates) if candidates else None


def filter_records_by_date_range(
    records: Sequence[Row],
    start_date: date,
    end_date: date,
    *,
    include_undated: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    undated_count = 0
    for record in records:
        relevant_date = _record_relevant_date(record)
        if relevant_date is None:
            undated_count += 1
            if include_undated:
                filtered.append(dict(record))
            continue
        if start_date <= relevant_date <= end_date:
            filtered.append(dict(record))
    return filtered, undated_count


def _record_sort_dates(record: Row) -> tuple[date | None, date | None]:
    date_reported = _record_relevant_date({"date_reported": record.get("date_reported", "")})
    breach_candidates = [
        parsed
        for token in ISO_DATE_RE.findall(str(record.get("date_of_breach", "") or ""))
        for parsed in [_safe_iso_date(token)]
        if parsed is not None
    ]
    date_of_breach = max(breach_candidates) if breach_candidates else None
    return date_reported, date_of_breach


def _sort_records(records: Sequence[Row]) -> list[dict[str, Any]]:
    def _sort_key(record: Row) -> tuple[int, int, int, str]:
        date_reported, date_of_breach = _record_sort_dates(record)
        primary_date = date_reported or date_of_breach
        return (
            0 if primary_date is not None else 1,
            -(primary_date.toordinal() if primary_date is not None else 0),
            -(date_of_breach.toordinal() if date_of_breach is not None else 0),
            str(record.get("organization_name", "") or "").casefold(),
        )

    return [dict(record) for record in sorted(records, key=_sort_key)]


def _annotate_record(record: Row, source: SourceDefinition) -> dict[str, Any]:
    annotated = dict(record)
    annotated["source"] = source.display_name
    annotated["source_key"] = source.key
    annotated["_source_name"] = source.display_name
    annotated["_source_key"] = source.key
    annotated["_source_url"] = source.default_url
    return annotated


def _selected_sources(source_keys: Iterable[str] | None = None) -> list[SourceDefinition]:
    sources = list(list_sources())
    if not source_keys:
        return sources

    requested = {key.strip() for key in source_keys if key and key.strip()}
    available = {source.key: source for source in sources}
    missing = sorted(requested - available.keys())
    if missing:
        raise ValueError(
            "Unknown source key(s): "
            + ", ".join(missing)
            + ". Use --list-sources to see valid keys."
        )
    return [available[key] for key in sorted(requested)]


def scrape_all_sources(
    start_date: date,
    end_date: date,
    *,
    source_keys: Iterable[str] | None = None,
    include_undated: bool = False,
    strict: bool = False,
    user_agent: str | None = None,
    retries: int = 3,
) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    failures: list[str] = []
    for source in _selected_sources(source_keys):
        try:
            LOGGER.info("Fetching source %s", source.key)
            html = source.fetch_html(
                source.default_url,
                start_date=start_date,
                end_date=end_date,
                user_agent=user_agent,
                retries=retries,
            )
            records = source.parse_html(html, source.default_url)
            filtered_records, undated_count = filter_records_by_date_range(
                records, start_date, end_date, include_undated=include_undated
            )
            LOGGER.info(
                "Source %s produced %d records (%d kept, %d undated)",
                source.key,
                len(records),
                len(filtered_records),
                undated_count,
            )
            combined.extend(_annotate_record(record, source) for record in filtered_records)
        except Exception as exc:
            message = f"source {source.key} failed: {exc}"
            failures.append(message)
            LOGGER.error(message)
            if strict:
                raise SourceRunError(message) from exc

    if failures and not combined and strict:
        raise SourceRunError("; ".join(failures))

    return _sort_records(combined)


def scrape_offline(
    input_html: str,
    start_date: date,
    end_date: date,
    *,
    source_keys: Iterable[str] | None = None,
    include_undated: bool = False,
) -> list[dict[str, Any]]:
    selected = _selected_sources(source_keys)
    if len(selected) != 1:
        raise ValueError(
            "--input-html requires exactly one --source (the source whose saved page this is)."
        )
    source = selected[0]
    html = Path(input_html).read_text(encoding="utf-8")
    records = source.parse_html(html, source.default_url)
    filtered_records, _ = filter_records_by_date_range(
        records, start_date, end_date, include_undated=include_undated
    )
    return _sort_records([_annotate_record(record, source) for record in filtered_records])


def _print_sources() -> int:
    for source in sorted(list_sources(), key=lambda item: item.key):
        print(f"{source.key}\t{source.display_name}\t{source.default_url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    try:
        if args.list_sources:
            return _print_sources()

        start_date, end_date = resolve_date_range(args.start_date, args.end_date)
        if args.input_html:
            records = scrape_offline(
                args.input_html,
                start_date,
                end_date,
                source_keys=args.sources,
                include_undated=args.include_undated,
            )
        else:
            records = scrape_all_sources(
                start_date,
                end_date,
                source_keys=args.sources,
                include_undated=args.include_undated,
                strict=args.strict,
                user_agent=args.user_agent,
                retries=args.retries,
            )
        write_output(records, output_format=args.output, out_file=args.out_file)
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
