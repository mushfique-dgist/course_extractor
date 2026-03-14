#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36 course-extractor/1.0"
)

FIELD_PATTERNS = {
    "course_code": [r"course\s*(number|code)", r"class\s*(number|code)", r"subject"],
    "instructors": [r"instructor", r"faculty", r"professor"],
    "term": [r"term", r"semester", r"session"],
    "schedule": [r"schedule", r"meeting", r"class time", r"days?"],
    "duration": [r"duration", r"length", r"weeks?"],
    "credits": [r"credits?", r"units?"],
    "department": [r"department", r"division", r"subject area"],
    "location": [r"location", r"campus", r"venue"],
    "delivery_mode": [r"format", r"modality", r"online", r"in[- ]?person", r"hybrid"],
    "prerequisites": [r"prerequisite", r"requirements?"],
    "tuition": [r"tuition", r"fees?", r"cost", r"price"],
    "application_deadline": [r"deadline", r"application due", r"registration due"],
    "description": [r"description", r"overview", r"summary"],
}

COURSE_TYPE_NAMES = {"course", "courseinstance", "educationaloccupationalprogram", "event"}
COURSE_HINTS = ("course", "courses", "class", "program", "catalog", "summer", "subject")
NAV_BLOCK = {
    "home",
    "about",
    "contact",
    "admissions",
    "faq",
    "news",
    "events",
    "help",
    "privacy",
    "terms",
    "login",
    "sign in",
    "register",
}

CODE_RE = re.compile(r"\b[A-Z]{2,}\s?-?\d{1,4}[A-Z]?\b")
DATE_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?\b",
    flags=re.IGNORECASE,
)
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
CREDIT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:credits?|units?)\b", flags=re.IGNORECASE)


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string without microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(text: str) -> str:
    """Collapse whitespace and strip a string, returning empty string for falsy input."""
    return re.sub(r"\s+", " ", text or "").strip()


def uniq(values: list[str]) -> list[str]:
    """Return deduplicated list preserving order, comparing case-insensitively."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = clean(value)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def strip_fragment(url: str) -> str:
    """Remove the fragment (hash) portion from a URL."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def same_domain(base_url: str, target_url: str) -> bool:
    """Check whether two URLs share the same domain or are subdomains of each other."""
    base = urlparse(base_url).netloc.lower().split(":")[0]
    target = urlparse(target_url).netloc.lower().split(":")[0]
    return base == target or target.endswith("." + base) or base.endswith("." + target)


def read_urls(path: Path) -> list[str]:
    """Read URLs from a text file, one per line, ignoring comments and blanks."""
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    urls = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    urls = [u for u in urls if u and not u.startswith("#")]
    return uniq(urls)


def fetch_html(session: requests.Session, url: str, timeout: float) -> dict[str, Any]:
    """Fetch a URL and return a dict with ok status, HTML content, and error info."""
    try:
        res = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return {"ok": False, "source_url": url, "final_url": url, "status": None, "html": "", "error": str(exc)}
    ctype = res.headers.get("content-type", "").lower()
    html = res.text or ""
    is_html = "text/html" in ctype or "application/xhtml+xml" in ctype or html.lstrip().startswith("<")
    return {
        "ok": True,
        "source_url": url,
        "final_url": res.url,
        "status": res.status_code,
        "html": html if is_html else "",
        "error": "" if is_html else "Response was not HTML content",
    }


def meta(soup: BeautifulSoup, *names: str) -> str:
    """Extract the content of the first matching meta tag by name or property."""
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return clean(str(tag.get("content")))
    return ""


def html_text(fragment: str) -> str:
    """Parse an HTML fragment and return its visible text content."""
    if not fragment:
        return ""
    soup = BeautifulSoup(fragment, "html.parser")
    return clean(soup.get_text(" ", strip=True))


def flatten_json(node: Any) -> list[dict[str, Any]]:
    """Recursively collect all dict objects from a nested JSON structure."""
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        out.append(node)
        for value in node.values():
            out.extend(flatten_json(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(flatten_json(item))
    return out


def parse_jsonld_courses(soup: BeautifulSoup, input_url: str, discovered_from: str, text_excerpt: str) -> list[dict[str, Any]]:
    """Extract course records from JSON-LD structured data embedded in the page."""
    out: list[dict[str, Any]] = []
    for script in soup.find_all(
        "script",
        attrs={"type": lambda v: isinstance(v, str) and "ld+json" in v.lower()},
    ):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in flatten_json(payload):
            typ = obj.get("@type")
            if isinstance(typ, list):
                types = {str(t).lower() for t in typ}
            else:
                types = {str(typ).lower()} if typ else set()
            if not types.intersection(COURSE_TYPE_NAMES):
                continue
            provider = obj.get("provider")
            university = ""
            if isinstance(provider, dict):
                university = clean(str(provider.get("name", "")))
            out.append(
                {
                    "source_input_url": input_url,
                    "discovered_from_url": discovered_from,
                    "course_url": clean(str(obj.get("url", ""))),
                    "title": clean(str(obj.get("name", ""))),
                    "course_code": clean(str(obj.get("courseCode", ""))),
                    "description": clean(str(obj.get("description", ""))),
                    "university": university,
                    "department": "",
                    "term": clean(str(obj.get("temporalCoverage", ""))),
                    "schedule": clean(" - ".join(v for v in [str(obj.get("startDate", "")), str(obj.get("endDate", ""))] if clean(v))),
                    "duration": clean(str(obj.get("timeRequired", ""))),
                    "credits": clean(str(obj.get("numberOfCredits", ""))),
                    "instructors": [],
                    "location": "",
                    "delivery_mode": clean(str(obj.get("courseMode", ""))),
                    "prerequisites": clean(str(obj.get("competencyRequired", ""))),
                    "tuition": "",
                    "application_deadline": clean(str(obj.get("applicationDeadline", ""))),
                    "important_dates": [],
                    "tags": [],
                    "raw_text_excerpt": text_excerpt,
                }
            )
    return out


def detect_fose_catalog(page_html: str, page_url: str, soup: BeautifulSoup) -> dict[str, Any] | None:
    """Detect a FOSE-powered course catalog and return its API config, or None."""
    if "foseConfig" not in page_html or "apiURL" not in page_html:
        return None
    api_match = re.search(r'apiURL:\s*"([^"]+)"', page_html)
    if not api_match:
        return None

    group_field = "custom_code"
    key_field = "crn"
    group_match = re.search(r'"groupByPrimary":"([^"]+)"', page_html)
    key_match = re.search(r'"keyField":"([^"]+)"', page_html)
    if group_match:
        group_field = group_match.group(1).split(",")[0]
    if key_match:
        key_field = key_match.group(1)

    srcdb_options: list[dict[str, str]] = []
    term_select = soup.find("select", id="crit-srcdb")
    if term_select:
        for option in term_select.find_all("option"):
            code = clean(option.get("value") or "")
            if not code:
                continue
            srcdb_options.append({"code": code, "name": clean(option.get_text(" ", strip=True))})
    if not srcdb_options:
        return None

    return {
        "api_url": urljoin(page_url, api_match.group(1)),
        "group_field": group_field,
        "key_field": key_field,
        "srcdb_options": srcdb_options,
    }


def fose_api_call(
    session: requests.Session,
    api_url: str,
    route: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """Make a POST request to a FOSE catalog API endpoint and return the JSON response."""
    joiner = "&" if "?" in api_url else "?"
    url = f"{api_url}{joiner}route={route}"
    encoded_payload = quote(json.dumps(payload, separators=(",", ":")))
    response = session.post(
        url,
        data=encoded_payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def parse_fose_courses(
    session: requests.Session,
    page_html: str,
    page_url: str,
    input_url: str,
    timeout: float,
    max_api_courses_per_term: int,
    include_api_details: bool,
    all_fose_terms: bool,
    delay_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract courses via the FOSE catalog API, returning (pages, courses) tuples."""
    soup = BeautifulSoup(page_html, "html.parser")
    config = detect_fose_catalog(page_html, page_url, soup)
    if not config:
        return [], []

    pages: list[dict[str, Any]] = []
    courses: list[dict[str, Any]] = []
    api_url = str(config["api_url"])
    group_field = str(config["group_field"])
    key_field = str(config["key_field"])
    srcdb_options = config["srcdb_options"]
    target_terms = srcdb_options if all_fose_terms else srcdb_options[:1]

    pages.append(
        {
            "source_url": input_url,
            "final_url": page_url,
            "status_code": 200,
            "fetched_at_utc": now_iso(),
            "page_type": "fose_catalog",
            "page_title": clean(soup.title.get_text(" ", strip=True) if soup.title else ""),
            "meta_description": meta(soup, "description", "og:description", "twitter:description"),
            "site_name": meta(soup, "og:site_name", "application-name"),
            "headings": {"h1": uniq([h.get_text(" ", strip=True) for h in soup.find_all("h1")]), "h2": []},
            "field_candidates": {
                "api_url": [api_url],
                "srcdb_codes_available": [t["code"] for t in srcdb_options],
                "srcdb_codes_used": [t["code"] for t in target_terms],
            },
            "course_link_candidates": [],
            "errors": [],
        }
    )

    for srcdb in target_terms:
        code = srcdb["code"]
        term_name = srcdb["name"]
        try:
            search_payload = {"other": {"srcdb": code}, "criteria": []}
            search_data = fose_api_call(session, api_url, "search", search_payload, timeout)
        except Exception as exc:  # noqa: BLE001
            pages.append(
                {
                    "source_url": page_url,
                    "final_url": f"{api_url}&route=search",
                    "status_code": None,
                    "fetched_at_utc": now_iso(),
                    "page_type": "error",
                    "page_title": f"FOSE term {code}",
                    "meta_description": "",
                    "site_name": "",
                    "headings": {"h1": [], "h2": []},
                    "field_candidates": {"term": [term_name]},
                    "course_link_candidates": [],
                    "errors": [f"FOSE search failed for {code}: {exc}"],
                }
            )
            continue

        rows = list(search_data.get("results", []) or [])
        total_rows = len(rows)
        if max_api_courses_per_term > 0:
            rows = rows[:max_api_courses_per_term]

        pages.append(
            {
                "source_url": page_url,
                "final_url": f"{api_url}&route=search&srcdb={code}",
                "status_code": 200,
                "fetched_at_utc": now_iso(),
                "page_type": "fose_api_term",
                "page_title": term_name,
                "meta_description": "",
                "site_name": "",
                "headings": {"h1": [term_name], "h2": []},
                "field_candidates": {"srcdb": [code], "rows_returned": [str(total_rows)], "rows_used": [str(len(rows))]},
                "course_link_candidates": [],
                "errors": [],
            }
        )

        for row in rows:
            detail: dict[str, Any] = {}
            if include_api_details:
                group_value = clean(str(row.get(group_field, "") or row.get("custom_code", "") or row.get("code", "")))
                key_value = clean(str(row.get(key_field, "") or row.get("crn", "") or row.get("key", "")))
                if group_value and key_value:
                    try:
                        detail_payload = {
                            "group": f"{group_field}:{group_value}",
                            "key": f"{key_field}:{key_value}",
                            "srcdb": code,
                            "matched": f"{key_field}:{key_value}",
                        }
                        detail = fose_api_call(session, api_url, "details", detail_payload, timeout)
                    except Exception:  # noqa: BLE001
                        detail = {}

            title = clean(str(detail.get("title", "") or row.get("title", "")))
            code_value = clean(
                str(detail.get("custom_code", "") or detail.get("code", "") or row.get("custom_code", "") or row.get("code", ""))
            )
            section = clean(str(detail.get("section", "") or row.get("no", "")))
            crn = clean(str(detail.get("crn", "") or row.get("crn", "")))
            meeting = html_text(str(detail.get("meeting_html", ""))) or clean(str(row.get("meets", "")))
            description = html_text(str(detail.get("description", "")))
            prereqs = html_text(str(detail.get("prereqs", "")))
            tuition = html_text(str(detail.get("tuition", "")))
            drop_deadlines_text = html_text(str(detail.get("drop_deadlines", "")))
            schedule_dates = clean(
                " - ".join(v for v in [str(row.get("start_date", "")), str(row.get("end_date", ""))] if clean(v))
            )

            instructor_html = str(detail.get("instructor_info_html", ""))
            instructor_doc = BeautifulSoup(instructor_html, "html.parser")
            instructors = uniq([a.get_text(" ", strip=True) for a in instructor_doc.find_all("a")])
            if not instructors:
                instructor_text = html_text(instructor_html) or clean(str(row.get("instr", "")))
                instructors = uniq([i for i in re.split(r",|;|\\band\\b|&", instructor_text) if clean(i)])
            important_dates = uniq(DATE_RE.findall(f"{drop_deadlines_text} {schedule_dates}"))
            deadline_match = re.search(
                r"Last day to register:\s*(.+?)(?:\s+Last day to|$)",
                drop_deadlines_text,
                flags=re.IGNORECASE,
            )

            courses.append(
                {
                    "source_input_url": input_url,
                    "discovered_from_url": page_url,
                    "course_url": f"{page_url}#srcdb={code}&crn={crn}",
                    "title": title,
                    "course_code": code_value,
                    "description": description,
                    "university": meta(soup, "og:site_name", "application-name") or clean(urlparse(page_url).netloc),
                    "department": "",
                    "term": term_name,
                    "schedule": meeting,
                    "duration": schedule_dates,
                    "credits": html_text(str(detail.get("hours_html", ""))) or clean(str(row.get("hours", ""))),
                    "instructors": instructors,
                    "location": "",
                    "delivery_mode": clean(str(detail.get("schd_name", "") or row.get("schd", ""))),
                    "prerequisites": prereqs,
                    "tuition": tuition,
                    "application_deadline": clean(deadline_match.group(1)) if deadline_match else "",
                    "important_dates": important_dates,
                    "tags": uniq(
                        [
                            clean(str(detail.get("credit_status_name", ""))),
                            clean(str(detail.get("section_status", "") or row.get("stat", ""))),
                            clean(str(detail.get("part_of_term", ""))),
                            clean(str(detail.get("schd_name", ""))),
                            f"srcdb:{code}",
                            f"section:{section}",
                        ]
                    ),
                    "raw_text_excerpt": clean(
                        " ".join(
                            [
                                title,
                                description,
                                meeting,
                                prereqs,
                                drop_deadlines_text,
                                html_text(str(detail.get("notes_html", ""))),
                            ]
                        )
                    )[:5000],
                }
            )
            if delay_seconds > 0 and include_api_details:
                time.sleep(delay_seconds)

    return pages, courses


def label_pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Extract label-value pairs from dt/dd, th/td, and colon-separated text patterns."""
    pairs: list[tuple[str, str]] = []
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            pairs.append((clean(dt.get_text(" ", strip=True)), clean(dd.get_text(" ", strip=True))))
    for tr in soup.find_all("tr"):
        th = tr.find_all("th")
        td = tr.find_all("td")
        if len(th) == 1 and td:
            pairs.append((clean(th[0].get_text(" ", strip=True)), clean(" | ".join(c.get_text(" ", strip=True) for c in td))))
    for el in soup.find_all(["p", "li", "div", "span"]):
        t = clean(el.get_text(" ", strip=True))
        if not t or len(t) > 220:
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9 /&().,'-]{1,50}):\s+(.+)$", t)
        if m:
            pairs.append((clean(m.group(1)), clean(m.group(2))))
    dedup: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for k, v in pairs:
        key = (k.lower(), v.lower())
        if key not in seen:
            seen.add(key)
            dedup.append((k, v))
    return dedup


def fields_from_pairs(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Match label-value pairs against known field patterns and return grouped values."""
    out: dict[str, list[str]] = {k: [] for k in FIELD_PATTERNS}
    for label, value in pairs:
        low = label.lower()
        for field, patterns in FIELD_PATTERNS.items():
            if any(re.search(p, low) for p in patterns):
                out[field].append(value)
    return {k: uniq(v) for k, v in out.items() if v}


def course_links(soup: BeautifulSoup, page_url: str, same_domain_only: bool, max_links: int) -> list[dict[str, Any]]:
    """Score and return links likely pointing to course pages, sorted by relevance."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.find_all("a"):
        href = clean(a.get("href") or "")
        text = clean(a.get_text(" ", strip=True))
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        url = strip_fragment(urljoin(page_url, href))
        if urlparse(url).scheme not in {"http", "https"}:
            continue
        if same_domain_only and not same_domain(page_url, url):
            continue
        text_low = text.lower()
        href_low = url.lower()
        if text_low in NAV_BLOCK:
            continue
        score = 0
        score += 2 * sum(1 for h in COURSE_HINTS if h in href_low)
        score += 2 * sum(1 for h in COURSE_HINTS if h in text_low)
        if CODE_RE.search(text):
            score += 3
        if re.search(r"/(course|courses|class|program|catalog)/", href_low):
            score += 2
        if len(text) >= 6:
            score += 1
        if score < 3 or url in seen:
            continue
        seen.add(url)
        found.append({"url": url, "anchor_text": text, "score": score})
    found.sort(key=lambda x: (-int(x["score"]), x["url"]))
    return found[:max_links]


def analyze_page(
    html: str,
    source_url: str,
    final_url: str,
    status: int | None,
    source_input_url: str,
    discovered_from_url: str,
    same_domain_only: bool,
    max_links: int,
    max_text_chars: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Analyze an HTML page to extract course data, metadata, and outbound course links.

    Returns a (page_info, courses, links) tuple. Uses JSON-LD when available,
    falling back to heuristic field extraction from HTML structure.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    h1 = uniq([h.get_text(" ", strip=True) for h in soup.find_all("h1")])
    h2 = uniq([h.get_text(" ", strip=True) for h in soup.find_all("h2")[:12]])
    pairs = label_pairs(soup)
    fields = fields_from_pairs(pairs)
    text_soup = BeautifulSoup(html, "html.parser")
    for bad in text_soup(["script", "style", "noscript", "svg"]):
        bad.decompose()
    text_excerpt = clean(text_soup.get_text("\n", strip=True))[:max_text_chars]
    links = course_links(soup, final_url, same_domain_only, max_links)
    listing_hit = any(x in f"{title} {' '.join(h1)}".lower() for x in ["courses", "catalog", "browse", "search"])
    detail_signals = sum(1 for k in ["instructors", "term", "schedule", "credits", "prerequisites", "tuition"] if fields.get(k))
    page_type = "listing" if (listing_hit and len(links) >= 8 and detail_signals <= 1) else "course_or_detail"

    courses = parse_jsonld_courses(soup, source_input_url, discovered_from_url, text_excerpt)
    heuristic_title = h1[0] if h1 else title
    code_hits = uniq((fields.get("course_code") or []) + CODE_RE.findall(f"{heuristic_title} {text_excerpt}"))
    money_hits = uniq((fields.get("tuition") or []) + MONEY_RE.findall(text_excerpt))
    credit_hits = uniq((fields.get("credits") or []) + CREDIT_RE.findall(text_excerpt))
    date_hits = uniq(DATE_RE.findall(text_excerpt))[:8]

    if page_type != "listing":
        courses.append(
            {
                "source_input_url": source_input_url,
                "discovered_from_url": discovered_from_url,
                "course_url": final_url,
                "title": heuristic_title,
                "course_code": code_hits[0] if code_hits else "",
                "description": (fields.get("description") or [meta(soup, "description", "og:description")])[0] if (fields.get("description") or [meta(soup, "description", "og:description")])[0] else "",
                "university": meta(soup, "og:site_name", "application-name"),
                "department": (fields.get("department") or [""])[0],
                "term": (fields.get("term") or [""])[0],
                "schedule": (fields.get("schedule") or [""])[0],
                "duration": (fields.get("duration") or [""])[0],
                "credits": credit_hits[0] if credit_hits else "",
                "instructors": [],
                "location": (fields.get("location") or [""])[0],
                "delivery_mode": (fields.get("delivery_mode") or [""])[0],
                "prerequisites": (fields.get("prerequisites") or [""])[0],
                "tuition": money_hits[0] if money_hits else "",
                "application_deadline": (fields.get("application_deadline") or [""])[0],
                "important_dates": date_hits,
                "tags": uniq([heuristic_title, (fields.get("term") or [""])[0], (fields.get("delivery_mode") or [""])[0]]),
                "raw_text_excerpt": text_excerpt,
            }
        )

    dedup_courses: list[dict[str, Any]] = []
    seen_ckey: set[tuple[str, str]] = set()
    for course in courses:
        key = (clean(course.get("course_url", "")).lower(), clean(course.get("title", "")).lower())
        if key in seen_ckey:
            continue
        seen_ckey.add(key)
        if not course.get("course_url"):
            course["course_url"] = final_url
        dedup_courses.append(course)

    page = {
        "source_url": source_url,
        "final_url": final_url,
        "status_code": status,
        "fetched_at_utc": now_iso(),
        "page_type": page_type,
        "page_title": title,
        "meta_description": meta(soup, "description", "og:description", "twitter:description"),
        "site_name": meta(soup, "og:site_name", "application-name"),
        "headings": {"h1": h1, "h2": h2},
        "field_candidates": fields,
        "course_link_candidates": links,
        "errors": [],
    }
    return page, dedup_courses, links


def process_input_url(
    session: requests.Session,
    input_url: str,
    timeout: float,
    crawl_listings: bool,
    max_links_per_listing: int,
    same_domain_only: bool,
    max_text_chars: int,
    delay_seconds: float,
    max_api_courses_per_term: int,
    include_api_details: bool,
    all_fose_terms: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Process a single input URL end-to-end: fetch, detect strategy, and extract courses.

    Tries FOSE API extraction first; falls back to HTML analysis with optional
    listing-page crawling. Returns (pages, courses).
    """
    pages: list[dict[str, Any]] = []
    courses: list[dict[str, Any]] = []

    root = fetch_html(session, input_url, timeout)
    if not root["ok"] or root["error"]:
        pages.append(
            {
                "source_url": input_url,
                "final_url": root["final_url"],
                "status_code": root["status"],
                "fetched_at_utc": now_iso(),
                "page_type": "error",
                "page_title": "",
                "meta_description": "",
                "site_name": "",
                "headings": {"h1": [], "h2": []},
                "field_candidates": {},
                "course_link_candidates": [],
                "errors": [root["error"]],
            }
        )
        return pages, courses

    fose_pages, fose_courses = parse_fose_courses(
        session=session,
        page_html=root["html"],
        page_url=root["final_url"],
        input_url=input_url,
        timeout=timeout,
        max_api_courses_per_term=max_api_courses_per_term,
        include_api_details=include_api_details,
        all_fose_terms=all_fose_terms,
        delay_seconds=delay_seconds,
    )
    if fose_pages:
        pages.extend(fose_pages)
        courses.extend(fose_courses)
        return pages, courses

    root_page, root_courses, links = analyze_page(
        root["html"],
        input_url,
        root["final_url"],
        root["status"],
        input_url,
        input_url,
        same_domain_only,
        max_links_per_listing,
        max_text_chars,
    )
    pages.append(root_page)
    courses.extend(root_courses)

    if not crawl_listings or root_page["page_type"] != "listing":
        return pages, courses

    visited = {strip_fragment(root_page["final_url"]).lower()}
    for item in links[:max_links_per_listing]:
        child = strip_fragment(item["url"])
        if child.lower() in visited:
            continue
        visited.add(child.lower())
        fetched = fetch_html(session, child, timeout)
        if not fetched["ok"] or fetched["error"]:
            pages.append(
                {
                    "source_url": child,
                    "final_url": fetched["final_url"],
                    "status_code": fetched["status"],
                    "fetched_at_utc": now_iso(),
                    "page_type": "error",
                    "page_title": "",
                    "meta_description": "",
                    "site_name": "",
                    "headings": {"h1": [], "h2": []},
                    "field_candidates": {},
                    "course_link_candidates": [],
                    "errors": [fetched["error"]],
                }
            )
            continue
        page, found_courses, _ = analyze_page(
            fetched["html"],
            child,
            fetched["final_url"],
            fetched["status"],
            input_url,
            root_page["final_url"],
            same_domain_only,
            0,
            max_text_chars,
        )
        pages.append(page)
        courses.extend(found_courses)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return pages, courses


def render_md(payload: dict[str, Any]) -> str:
    """Render the extraction results payload as a human-readable Markdown report."""
    lines = [
        "# Course Extraction Report",
        "",
        f"- Generated: {payload['generated_at_utc']}",
        f"- Input file: `{payload['input_file']}`",
        f"- Input URLs: {payload['stats']['input_urls']}",
        f"- Pages analyzed: {payload['stats']['pages_analyzed']}",
        f"- Courses extracted: {payload['stats']['courses_extracted']}",
        f"- Page errors: {payload['stats']['page_errors']}",
        "",
        "## Courses",
        "",
    ]
    if not payload["courses"]:
        lines.append("No course records extracted.")
        lines.append("")
    for i, c in enumerate(payload["courses"], 1):
        lines.extend(
            [
                f"### {i}. {c.get('title') or '(Untitled)'}",
                f"- Course URL: {c.get('course_url', '')}",
                f"- Source Input URL: {c.get('source_input_url', '')}",
                f"- Discovered From URL: {c.get('discovered_from_url', '')}",
                f"- Course Code: {c.get('course_code', '')}",
                f"- University: {c.get('university', '')}",
                f"- Term: {c.get('term', '')}",
                f"- Schedule: {c.get('schedule', '')}",
                f"- Duration: {c.get('duration', '')}",
                f"- Credits: {c.get('credits', '')}",
                f"- Location: {c.get('location', '')}",
                f"- Delivery Mode: {c.get('delivery_mode', '')}",
                f"- Prerequisites: {c.get('prerequisites', '')}",
                f"- Tuition: {c.get('tuition', '')}",
                f"- Application Deadline: {c.get('application_deadline', '')}",
                f"- Important Dates: {', '.join(c.get('important_dates', []))}",
                "",
                "Description:",
                "",
                c.get("description", ""),
                "",
            ]
        )
    lines.extend(["## Pages", ""])
    for page in payload["pages"]:
        lines.append(f"- `{page.get('final_url', page.get('source_url', ''))}` ({page.get('page_type', 'unknown')})")
        if page.get("errors"):
            lines.append(f"  - Errors: {' | '.join(page['errors'])}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """CLI entry point: parse arguments, run extraction pipeline, and write outputs."""
    parser = argparse.ArgumentParser(description="Extract course info from URLs in a text file.")
    parser.add_argument("--input", type=Path, default=Path("course_websites.txt"))
    parser.add_argument("--output-json", type=Path, default=Path("extracted_courses.json"))
    parser.add_argument("--output-md", type=Path, default=Path("extracted_courses.md"))
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--max-links-per-listing", type=int, default=12)
    parser.add_argument("--max-text-chars", type=int, default=5000)
    parser.add_argument("--delay-seconds", type=float, default=0.4)
    parser.add_argument("--no-crawl-listings", action="store_true")
    parser.add_argument("--allow-cross-domain", action="store_true")
    parser.add_argument(
        "--max-api-courses-per-term",
        type=int,
        default=0,
        help="If >0, limit FOSE/API catalog extraction to this many rows per term (0 means no limit).",
    )
    parser.add_argument(
        "--no-api-details",
        action="store_true",
        help="For FOSE/API catalogs, skip per-course detail API calls and keep summary fields only.",
    )
    parser.add_argument(
        "--all-fose-terms",
        action="store_true",
        help="For FOSE/API catalogs, fetch all listed terms instead of only the first/default term.",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_json = args.output_json.resolve()
    output_md = args.output_md.resolve()
    urls = read_urls(input_path)
    if not urls:
        raise ValueError(f"No URLs found in input file: {input_path}")

    same_domain_only = not args.allow_cross_domain
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    pages: list[dict[str, Any]] = []
    courses: list[dict[str, Any]] = []
    for url in urls:
        p, c = process_input_url(
            session=session,
            input_url=url,
            timeout=args.timeout,
            crawl_listings=not args.no_crawl_listings,
            max_links_per_listing=max(0, args.max_links_per_listing),
            same_domain_only=same_domain_only,
            max_text_chars=max(500, args.max_text_chars),
            delay_seconds=max(0.0, args.delay_seconds),
            max_api_courses_per_term=max(0, args.max_api_courses_per_term),
            include_api_details=not args.no_api_details,
            all_fose_terms=args.all_fose_terms,
        )
        pages.extend(p)
        courses.extend(c)

    dedup_courses: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for course in courses:
        key = (clean(course.get("course_url", "")).lower(), clean(course.get("title", "")).lower())
        if key in seen:
            continue
        seen.add(key)
        dedup_courses.append(course)

    payload = {
        "generated_at_utc": now_iso(),
        "input_file": str(input_path),
        "settings": {
            "timeout": args.timeout,
            "crawl_listings": not args.no_crawl_listings,
            "max_links_per_listing": args.max_links_per_listing,
            "same_domain_only": same_domain_only,
            "max_text_chars": args.max_text_chars,
            "delay_seconds": args.delay_seconds,
            "max_api_courses_per_term": args.max_api_courses_per_term,
            "include_api_details": not args.no_api_details,
            "all_fose_terms": args.all_fose_terms,
        },
        "input_urls": urls,
        "pages": pages,
        "courses": dedup_courses,
        "stats": {
            "input_urls": len(urls),
            "pages_analyzed": len(pages),
            "courses_extracted": len(dedup_courses),
            "page_errors": sum(1 for p in pages if p.get("errors")),
        },
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    output_md.write_text(render_md(payload), encoding="utf-8")

    print(f"Processed input URLs: {len(urls)}")
    print(f"Analyzed pages: {len(pages)}")
    print(f"Extracted courses: {len(dedup_courses)}")
    print(f"Page errors: {payload['stats']['page_errors']}")
    print(f"JSON output: {output_json}")
    print(f"Markdown output: {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
