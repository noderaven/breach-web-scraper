# breach-web-scraper

[![CI](https://github.com/noderaven/breach-web-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/noderaven/breach-web-scraper/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

A Python tool for scraping breach-notification websites and combining them into a
single summary. Runtime dependencies: none (standard library only).

## Sources

| Key | Source |
| --- | --- |
| `wa_atg` | Washington Attorney General |
| `ca_oag` | California Attorney General |
| `or_doj` | Oregon Department of Justice |
| `maine_ag` | Maine Attorney General |
| `hhs_ocr` | HHS OCR Breach Portal (federal) |

List them anytime with `breach-scraper --list-sources`.

## Install

```bash
pip install .
```

This installs the `breach-scraper` console command.

## Usage

```bash
# Combine all sources for the last 6 months (default), as JSON
breach-scraper

# A single source, an explicit date range, Markdown to a file
breach-scraper --source wa_atg --start-date 2025-01-01 --end-date 2025-06-30 \
  --output markdown --out-file breaches.md

# A readable text summary, or a self-contained sortable HTML dashboard
breach-scraper --source ca_oag --output report
breach-scraper --output html --out-file breaches.html

# Offline: parse a previously saved page for one source (no network)
breach-scraper --source wa_atg --input-html saved_page.html

# Override the User-Agent / retry count; fail hard if any source errors
breach-scraper --user-agent "my-agent/1.0" --retries 5 --strict --verbose
```

### Options

- `--source KEY` (repeatable): limit to specific sources. Default: all.
- `--list-sources`: print supported source keys and exit.
- `--start-date` / `--end-date` (`YYYY-MM-DD`): inclusive date window. Default: last 6 months.
- `--output {json,csv,markdown,report,html}`: output format. Default: `json`.
- `--out-file PATH`: write output to a file instead of stdout.
- `--input-html PATH`: parse a saved page offline (requires exactly one `--source`).
- `--user-agent STR`, `--retries N`: request tuning.
- `--include-undated`: keep records with no parseable date.
- `--strict`: exit non-zero if any source fails. `--verbose`: per-source progress on stderr.

If a source returns HTTP 403, the tool prints an actionable message; use
`--input-html` with a saved copy, a different network, or a different `--user-agent`.

## Output formats

- `json` / `csv` / `markdown`: the combined records (column names normalized to `snake_case`).
- `report`: a text summary with totals plus per-notice detail.
- `html`: a self-contained, sortable web dashboard (no external assets).

## Development

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
bandit -r breach_scraper
python -m unittest discover -s tests -v
```

## Notes

- Standard library only at runtime; sources fetch through a shared HTTP helper with
  retries, exponential backoff, and helpful HTTP 403 handling.
- Each source exposes `DEFAULT_URL`, `fetch_html`, and `parse_breach_table`, registered in
  `breach_scraper/registry.py`. Adding a source is a small, self-contained module.
- Scraping depends on the upstream pages' structure; if a site changes, that source's
  parser may need updates.
