# Course Extractor

A Python CLI tool that scrapes university course catalogs and produces structured JSON and Markdown output -- built for feeding course data into downstream pipelines and LLMs.

## What It Does

Given a list of university course catalog URLs, the tool automatically detects the best extraction strategy and pulls structured course records:

1. **FOSE API auto-detection** -- If the site runs a FOSE-powered catalog (e.g., Harvard DCE), the tool discovers the API endpoint from the page source and queries it directly for complete course data including descriptions, instructors, schedules, and tuition.
2. **JSON-LD parsing** -- Extracts `Course` and `CourseInstance` structured data from `<script type="application/ld+json">` tags.
3. **HTML heuristic extraction** -- Falls back to parsing label-value pairs from `dt/dd`, `th/td`, and colon-separated text, matched against known field patterns for course codes, instructors, credits, prerequisites, etc.
4. **Listing page crawling** -- Detects catalog listing pages and follows links to individual course pages.

## Features

- Multi-strategy extraction with automatic strategy selection
- Handles JavaScript-rendered FOSE catalogs via direct API calls (no browser needed)
- Extracts 20+ structured fields per course (title, code, description, instructors, schedule, credits, tuition, prerequisites, dates, etc.)
- Deduplicates courses across pages
- Polite crawling with configurable delays
- Dual output: machine-readable JSON + human-readable Markdown
- Single-file, no complex dependencies

## Validated Results

Tested against Harvard DCE (`courses.dce.harvard.edu`): **508 courses extracted, 0 errors**.

## Installation

```bash
pip install -r requirements.txt
```

Dependencies: `requests`, `beautifulsoup4`

## Usage

### Basic

Create a `course_websites.txt` file with one URL per line (lines starting with `#` are ignored):

```txt
https://courses.dce.harvard.edu/
https://example.edu/summer/programs
```

Run the extractor:

```bash
python extract_courses.py
```

### CLI Options

```bash
# Custom input/output paths
python extract_courses.py --input urls.txt --output-json out.json --output-md out.md

# FOSE catalogs: fetch all terms instead of just the default
python extract_courses.py --all-fose-terms

# FOSE catalogs: limit to 100 courses per term
python extract_courses.py --max-api-courses-per-term 100

# FOSE catalogs: skip detailed per-course API calls (faster, less data)
python extract_courses.py --no-api-details

# Disable listing-page crawling
python extract_courses.py --no-crawl-listings

# Allow following links to external domains
python extract_courses.py --allow-cross-domain

# Adjust request timeout and crawl delay
python extract_courses.py --timeout 30 --delay-seconds 0.5
```

### Example Output (JSON, abridged)

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

## How It Works

```
Input URLs (course_websites.txt)
        |
        v
  Fetch HTML page
        |
        v
  Is FOSE catalog? --yes--> Query FOSE API (search + details per course)
        |                          |
        no                         v
        |                   Structured course records
        v
  Has JSON-LD? --yes--> Parse structured data
        |                     |
        no                    v
        |              Course records
        v
  HTML heuristic extraction (label-value pairs, regex patterns)
        |
        v
  Is listing page? --yes--> Follow course links, extract each
        |
        v
  Deduplicate & merge
        |
        v
  Output JSON + Markdown
```

The tool is a single Python file (`extract_courses.py`, ~900 lines) with no framework dependencies beyond `requests` and `beautifulsoup4`.

## License

[MIT](LICENSE)
