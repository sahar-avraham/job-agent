"""LinkedIn's public page for one job, read by the job's number, for a job no other source gives the text of.

Only the job page is read, never a search: the number comes from the link a list of jobs already gave
(the Tech Map's), and the page is the one LinkedIn shows any visitor who is not signed in. No account,
no cookie. LinkedIn's terms forbid automated reading, so this stays small and stops at the first sign
of refusal (see scout.complete, which paces the requests and keeps the count low).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import date, timedelta

import fetch_jobs
import workday

JOB = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{number}"
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


def read(number: str) -> dict:
    """Ask for one job page. Raises Refused on any answer that means "stop"."""
    request = urllib.request.Request(JOB.format(number=number), headers={"User-Agent": AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=25, context=workday.CONTEXT) as response:
            final, page = response.geturl(), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        if error.code in (404, 410):
            return {"result": "gone"}
        raise Refused(f"HTTP {error.code}")
    if re.search(r"authwall|/login|/uas/", final):
        raise Refused("sign-in page")
    return parse(page)
