"""DevJobs (devjobs.co.il), an Israeli developer job board that carries LinkedIn postings with their full text.

It keeps each posting under its LinkedIn job number, with the whole text and the date it was last
updated, and its robots.txt allows every path. Two uses:

- A job known only from the Tech Map, whose link is a LinkedIn job, is looked up by that number
  (`full_text`, used by scout.py).
- A company whose own careers site cannot be read, such as one behind bot protection, is read from its
  company page here, as a board in companies.json: ["Example Ltd", "devjobs", "example-ltd"],
  the token being the company's address on the site (`fetch`).
"""

from __future__ import annotations

import functools
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import fetch_jobs
import workday

JOB = "https://www.devjobs.co.il/job-details/{number}"
COMPANY = "https://www.devjobs.co.il/company-details/{slug}"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
LINKEDIN_NUMBER = re.compile(r"linkedin\.com/jobs/view/\S*?-?(\d{8,})(?:\D|$)")
TEXT = re.compile(r'<div class="content-single">(.*?)</div>', re.S)
UPDATED = re.compile(r"Updated\s+([A-Z][a-z]{2} \d{1,2}, \d{4})")
# A job's card on a company page: its number and title, then its place and date.
CARD = re.compile(r'class="name-job"[^>]*href="https://www\.devjobs\.co\.il/job-details/(\d+)"[^>]*>\s*([^<]+?)\s*</a>'
                  r'.*?class="location-small">([^<]*)</span>\s*<span class="card-time">([^<]*)</span>', re.S)


def read(url: str) -> str:
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=25, context=workday.CONTEXT) as response:
        return response.read().decode("utf-8", "replace")


def day(text: str) -> str:
    try:
        return datetime.strptime(" ".join(text.split()), "%b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def page_text(number: str) -> tuple[str, str]:
    """A posting's full text and the date it was last updated, or two empty strings when the site lacks it."""
    try:
        page = read(JOB.format(number=number))
    except Exception:
        return "", ""
    body = TEXT.search(page)
    text = fetch_jobs.strip_html(body.group(1)) if body else ""
    if len(text) < 300:
        return "", ""
    updated = UPDATED.search(fetch_jobs.strip_html(page))
    return text, day(updated.group(1)) if updated else ""


def full_text(url: str) -> tuple[str, str]:
    """The full text and date of a LinkedIn job, found here by its number."""
    number = LINKEDIN_NUMBER.search(url or "")
    return page_text(number.group(1)) if number else ("", "")


@functools.lru_cache(maxsize=64)
def cards(slug: str) -> tuple[tuple[str, str, str, str], ...]:
    """A company page's jobs as (number, title, place, date), each once, read once per run."""
    found: dict[str, tuple[str, str, str, str]] = {}
    for number, title, place, when in CARD.findall(read(COMPANY.format(slug=slug))):
        found.setdefault(number, (number, " ".join(html.unescape(title).split()), " ".join(html.unescape(place).split()),
                                  day(when)))
    return tuple(found.values())


def known_descriptions() -> dict[str, str]:
    """Texts already stored for jobs from this site, so each posting is read once."""
    try:
        import sqlite3
        import store
        connection = sqlite3.connect(store.DB_PATH)
        rows = connection.execute("SELECT url, description FROM jobs WHERE url LIKE '%devjobs.co.il/job-details/%'"
                                  " AND description != ''").fetchall()
        connection.close()
        return dict(rows)
    except Exception:
        return {}


def fetch(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read one company's jobs from its page here. Texts only for new jobs whose title passes the hard rules."""
    jobs = [fetch_jobs.Job(company=company, title=title, location=f"{place}, Israel" if place else "Israel",
                           posted=when, url=JOB.format(number=number))
            for number, title, place, when in cards(token)]
    if descriptions:
        known = known_descriptions()

        def describe(job: fetch_jobs.Job) -> fetch_jobs.Job:
            if job.url in known:
                job.description = known[job.url]
            elif workday.worth_reading(job.title):
                text, updated = page_text(job.url.rsplit("/", 1)[-1])
                job.description = text
                job.posted = updated or job.posted
            return job

        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = list(pool.map(describe, jobs))
    return jobs
