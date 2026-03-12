# breach-web-scraper
A Python tool for scraping breach websites to provide a nice summary.

## WA AG scraper (initial source)
This repository includes a scraper for Washington Attorney General data breach notifications:

- Source URL: `https://www.atg.wa.gov/data-breach-notifications`
- Script: `scraper/wa_atg_scraper.py`

### Usage
```bash
python scraper/wa_atg_scraper.py --output json --limit 10
python scraper/wa_atg_scraper.py --output markdown --out-file wa_breaches.md
python scraper/wa_atg_scraper.py --output csv --out-file wa_breaches.csv
```

If your network gets blocked with HTTP 403, scrape from a saved HTML file:

```bash
python scraper/wa_atg_scraper.py --input-html saved_wa_page.html --output json
```

### Output fields
The parser normalizes column names from the HTML table to `snake_case`. For cells containing links, it also emits a `<column>_url` field.

### Known hurdles / maintenance notes
- Some environments may receive HTTP 403 from WA AG. The script now tries both `www.atg.wa.gov` and `atg.wa.gov`, sends browser-like headers, and gives a clear troubleshooting error.
- The scraper depends on the page containing a parseable HTML table.
- If WA AG changes table structure or field names, parsing/normalization may need updates.
- For production automation, add retries/backoff, persistence, and monitoring around this script.

### Tests
```bash
python -m unittest discover -s tests
```
