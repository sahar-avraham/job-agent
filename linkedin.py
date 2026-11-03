"""LinkedIn's public job search and its page for one job, both as any visitor who is not signed in.

The search finds the jobs at companies no board reads and no list gives; the job page gives a job's text,
for those and for the Tech Map's jobs, by the job's number. No account, no cookie. This stays small and stops
at the first sign of refusal (see scout.complete, which paces the requests and keeps the count low).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

import fetch_jobs
import workday

JOB = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{number}"
SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
# The roles searched for each day, in Israel, posted in the past week, at internship, entry and associate level.
QUERIES = ["Backend Developer", "Full Stack Developer", "Software Engineer", "DevOps Engineer",
           "Junior Developer", "Junior Software Engineer", "AI Engineer", "Graduate Software Engineer"]
SEARCH_FILTERS = {"location": "Israel", "geoId": "101620260", "f_TPR": "r604800", "f_E": "1,2,3"}
PAGE_SIZE = 10
CARD = re.compile(r"<li>(.*?)</li>", re.S)
CARD_FIELDS = {"title": re.compile(r'base-search-card__title[^>]*>(.*?)</h3>', re.S),
               "company": re.compile(r'base-search-card__subtitle[^>]*>(.*?)</h4>', re.S),
               "location": re.compile(r'job-search-card__location[^>]*>(.*?)</span>', re.S),
               "url": re.compile(r'base-card__full-link[^>]*href="([^"?]*)'),
               "posted": re.compile(r'<time[^>]*datetime="([^"]*)"')}
NUMBER = re.compile(r"linkedin\.com/jobs/view/\S*?-?(\d{8,})(?:\D|$)")
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DESCRIPTION = re.compile(r'show-more-less-html__markup[^>]*>(.*?)</div>', re.S)
POSTED = re.compile(r'posted-time-ago__text[^>]*>(.*?)</span>', re.S)
CRITERIA = re.compile(r'description__job-criteria-subheader[^>]*>(.*?)</h3>\s*<span[^>]*description__job-criteria-text[^>]*>(.*?)</span>', re.S)
CLOSED = re.compile(r"No longer accepting applications", re.I)
AGO = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.I)
DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


class Refused(Exception):
    """LinkedIn answered with a block, a rate limit or a sign-in page: stop asking for now."""


def number_of(url: str) -> str:
    found = NUMBER.search(url or "")
    return found.group(1) if found else ""


def posted_on(text: str, today: date | None = None) -> str:
    """The date a page's "3 days ago" stands for."""
    found = AGO.search(text or "")
    if not found:
        return ""
    return ((today or date.today()) - timedelta(days=int(found.group(1)) * DAYS[found.group(2).lower()])).isoformat()


def parse(page: str, today: date | None = None) -> dict:
    """Read a job page into its text, posting date and state. "closed" when it takes no more applicants,
    "unparsed" when its text cannot be found, which is how a change in the page shows."""
    if CLOSED.search(page):
        return {"result": "closed"}
    body = DESCRIPTION.search(page)
    text = fetch_jobs.strip_html(body.group(1)) if body else ""
    if len(text) < 200:
        return {"result": "unparsed"}
    criteria = " ".join(f"{fetch_jobs.strip_html(k)}: {fetch_jobs.strip_html(v)}." for k, v in CRITERIA.findall(page))
    posted = POSTED.search(page)
    return {"result": "text", "text": (text + (" " + criteria if criteria else "")).strip(),
            "posted": posted_on(fetch_jobs.strip_html(posted.group(1)), today) if posted else ""}


def parse_cards(page: str) -> list[dict]:
    """Read a page of search results into each job's title, company, place, link and posting date."""
    cards = []
    for block in CARD.findall(page):
        card = {field: (found.group(1).strip() if (found := pattern.search(block)) else "")
                for field, pattern in CARD_FIELDS.items()}
        for field in ("title", "company", "location"):
            card[field] = " ".join(fetch_jobs.strip_html(card[field]).split())
        if card["url"] and card["title"] and number_of(card["url"]):
            cards.append(card)
    return cards


def ask(url: str) -> str:
    """One page as a visitor who is not signed in. Raises Refused on any answer that means "stop"."""
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=25, context=workday.CONTEXT) as response:
        final, page = response.geturl(), response.read().decode("utf-8", "replace")
    if re.search(r"authwall|/login|/uas/", final):
        raise Refused("sign-in page")
    return page


def search(keywords: str, start: int = 0) -> list[dict]:
    """One page of the public job search, PAGE_SIZE jobs from start."""
    try:
        page = ask(SEARCH + urllib.parse.urlencode({"keywords": keywords, **SEARCH_FILTERS, "start": start}))
    except urllib.error.HTTPError as error:
        raise Refused(f"HTTP {error.code} on the search")
    return parse_cards(page)


def read(number: str) -> dict:
    """Ask for one job page. Raises Refused on any answer that means "stop"."""
    try:
        page = ask(JOB.format(number=number))
    except urllib.error.HTTPError as error:
        if error.code in (404, 410):
            return {"result": "gone"}
        raise Refused(f"HTTP {error.code}")
    return parse(page)
