<p align="center">
  <h1 align="center">Course Extractor</h1>
  <p align="center">
    <strong>Scrape any university course catalog into structured data — automatically.</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.8+">
    <img src="https://img.shields.io/badge/courses_extracted-508-success?style=flat-square" alt="508 courses">
    <img src="https://img.shields.io/badge/errors-0-brightgreen?style=flat-square" alt="0 errors">
    <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT License">
  </p>
</p>

---

A Python CLI tool that scrapes university course catalogs and produces structured **JSON** and **Markdown** output — built for feeding course data into downstream pipelines and LLMs.

> **Validated:** 508 courses extracted from Harvard DCE with zero errors.

## Highlights

- **Multi-strategy extraction** — Automatically selects the best scraping approach per site
- **FOSE API auto-detection** — Discovers hidden API endpoints in JavaScript-rendered catalogs (no browser needed)
- **JSON-LD + HTML heuristic fallback** — Handles structured data and raw HTML label-value parsing
- **20+ fields per course** — Title, code, description, instructors, schedule, credits, tuition, prerequisites, dates, and more
- **Deduplication** — Merges duplicate records across listing pages
- **Polite crawling** — Configurable delays and timeouts
- **Single file, minimal deps** — One Python file (~900 lines), only `requests` + `beautifulsoup4`

## How It Works

```
Input URLs (course_websites.txt)
        |
        v
  Fetch HTML page
        |
        v
  Is FOSE catalog? ──yes──> Query FOSE API (search + details per course)
        |                          |
        no                         v
        |                   Structured course records
        v
  Has JSON-LD? ──yes──> Parse structured data
        |                     |
        no                    v
        |              Course records
        v
  HTML heuristic extraction (label-value pairs, regex patterns)
        |
        v
  Is listing page? ──yes──> Follow course links, extract each
        |
        v
  Deduplicate & merge
        |
        v
  Output JSON + Markdown
```

## Quick Start

```bash
pip install -r requirements.txt
```

Create a `course_websites.txt` with one URL per line:

```txt
https://courses.dce.harvard.edu/
https://example.edu/summer/programs
```

Run it:

```bash
python extract_courses.py
```

### CLI Options

| Flag | Description |
|---|---|
| `--input FILE` | Custom input file (default: `course_websites.txt`) |
| `--output-json FILE` | JSON output path |
| `--output-md FILE` | Markdown output path |
| `--all-fose-terms` | Fetch all terms from FOSE catalogs |
| `--max-api-courses-per-term N` | Limit courses per FOSE term |
| `--no-api-details` | Skip per-course API detail calls (faster) |
| `--no-crawl-listings` | Don't follow links on listing pages |
| `--allow-cross-domain` | Follow links to external domains |
| `--timeout N` | Request timeout in seconds |
| `--delay-seconds N` | Delay between requests |

### Example Output

```json
{
  "title": "Introduction to Computer Science",
  "course_code": "CSCI E-10",
  "university": "Harvard Division of Continuing Education",
  "term": "Spring 2025",
  "schedule": "Mon Wed 6:30 PM - 8:00 PM",
  "credits": "4 credits",
  "instructors": ["Jane Smith"],
  "delivery_mode": "Online",
  "description": "An introduction to the intellectual enterprises of computer science...",
  "tuition": "$3,100",
  "prerequisites": "None"
}
```

## License

[MIT](LICENSE)
