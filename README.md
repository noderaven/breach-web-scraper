# breach-web-scraper

[![CI](https://github.com/noderaven/breach-web-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/noderaven/breach-web-scraper/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

A Python tool for scraping breach websites to provide a nice summary. The first
supported source is the Washington State Attorney General data breach
notifications page. Runtime dependencies: none (standard library only).

## Install

```bash
pip install .
```

This installs the `breach-scraper` console command.

## Usage

```bash
# Fetch live and print JSON (default), limited to 10 rows
breach-scraper --output json --limit 10

# Markdown / CSV to a file
breach-scraper --output markdown --out-file wa_breaches.md
breach-scraper --output csv --out-file wa_breaches.csv

# Offline: parse a previously saved page (no network)
breach-scraper --input-html saved_page.html --output json

# Override the User-Agent or retry count
breach-scraper --user-agent "my-agent/1.0" --retries 5
```

If the source returns HTTP 403, the tool prints an actionable message; use
`--input-html` with a saved copy of the page, a different network, or a
different `--user-agent`.

## Output fields

Column names from the HTML table are normalized to `snake_case`. Cells that
contain links also emit a `<column>_url` field.

## Development

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
bandit -r breach_scraper
python -m unittest discover -s tests -v
```

## Source / maintenance notes

- Source URL: `https://www.atg.wa.gov/data-breach-notifications`
- The scraper depends on the page exposing a parseable HTML table; if the WA AG
  changes the table structure or field names, parsing may need updates.
