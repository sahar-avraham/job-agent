"""Large employers whose jobs sit in a hiring system of their own, outside the boards the sweep reads.

Each one is a companies.json entry like any other board, so run.py collects them with the rest:

    ["Amazon", "amazon", "ISR"]                                     the country code to search
    ["Microsoft", "eightfold", "apply.careers.microsoft.com|microsoft.com"]   the site and its domain

Eightfold hosts the careers sites of several large employers (Microsoft, Qualcomm, Amdocs). Its search
answers only within a session the site's own page opened, so each read first loads that page.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import fetch_jobs
import workday

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
AMAZON_SEARCH = "https://www.amazon.jobs/en/search.json?country={country}&result_limit=100&offset={offset}"
EIGHTFOLD_PAGE = 10  # the most one search returns


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                       urllib.request.HTTPSHandler(context=workday.CONTEXT))


def read_json(session: urllib.request.OpenerDirector, url: str, timeout: float = 30.0):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
    with session.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def known_descriptions(fragment: str) -> dict[str, str]:
    """Descriptions already stored, so a job seen in an earlier run is not asked for again."""
    try:
        import sqlite3
        import store
        connection = sqlite3.connect(store.DB_PATH)
        rows = connection.execute("SELECT url, description FROM jobs WHERE url LIKE ? AND description != ''",
                                  (f"%{fragment}%",)).fetchall()
        connection.close()
        return dict(rows)
    except Exception:
        return {}


def amazon_day(text: str) -> str:
    """Amazon writes dates as "July  7, 2026"."""
    try:
        return datetime.strptime(" ".join((text or "").split()), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def fetch_amazon(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read Amazon's jobs in one country; the search gives each job's description with it."""
    session, posts, offset = opener(), [], 0
    while True:
        page = read_json(session, AMAZON_SEARCH.format(country=urllib.parse.quote(token), offset=offset))
        posts += page.get("jobs") or []
        offset += 100
        if offset >= page.get("hits", 0) or not page.get("jobs"):
            break
    return [fetch_jobs.Job(
        company=company,
        title=post.get("title", "").strip(),
        location=post.get("normalized_location") or post.get("location") or "Israel",
        posted=amazon_day(post.get("posted_date")),
        url="https://www.amazon.jobs" + post.get("job_path", ""),
        description=fetch_jobs.strip_html(" ".join(post.get(k) or "" for k in
                                                   ("description", "basic_qualifications", "preferred_qualifications")))
        if descriptions else "",
    ) for post in posts]


def in_israel(position: dict) -> bool:
    # The search also returns remote jobs based elsewhere, which name another country.
    places = " ".join(position.get("locations") or []) + " " + " ".join(position.get("standardizedLocations") or [])
    return bool(re.search(r"\bisrael\b|, IL\b", places, re.I))


def fetch_eightfold(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read one Eightfold careers site's jobs in Israel. Descriptions only for new jobs whose title passes the hard rules."""
    host, domain = token.split("|")
    session = opener()
    session.open(urllib.request.Request(f"https://{host}/careers", headers={"User-Agent": AGENT}), timeout=30).read()
    positions, start = [], 0
    while start < 1000:
        query = urllib.parse.urlencode({"domain": domain, "query": "", "location": "Israel", "start": start,
                                        "sort_by": "timestamp"})
        data = read_json(session, f"https://{host}/api/pcsx/search?{query}").get("data") or {}
        positions += data.get("positions") or []
        start += EIGHTFOLD_PAGE
        if start >= data.get("count", 0) or not data.get("positions"):
            break

    jobs = [fetch_jobs.Job(
        company=company,
        title=" ".join(position.get("name", "").split()),
        location="; ".join(position.get("locations") or []) or "Israel",
        posted=datetime.fromtimestamp(position["postedTs"], tz=timezone.utc).strftime("%Y-%m-%d")
        if position.get("postedTs") else "",
        url=f"https://{host}{position.get('positionUrl', '')}",
    ) for position in positions if in_israel(position)]

    if descriptions:
        known = known_descriptions(host)

        def describe(job: fetch_jobs.Job) -> fetch_jobs.Job:
            if job.url in known:
                job.description = known[job.url]
            elif workday.worth_reading(job.title):
                try:
                    number = job.url.rsplit("/", 1)[-1]
                    query = urllib.parse.urlencode({"position_id": number, "domain": domain, "hl": "en"})
                    detail = read_json(session, f"https://{host}/api/pcsx/position_details?{query}").get("data") or {}
                    job.description = fetch_jobs.strip_html(detail.get("jobDescription") or "")
                except Exception:
                    pass
            return job

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(describe, jobs))
    return jobs


ELBIT_JOBS = "https://{host}/cron/jobs.json"


def fetch_elbit(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read Elbit Systems' careers site, which publishes every open job, with its text, as one file.

    The site has no page per job, so each link carries the job's code for searching it on the jobs page.
    It names a region rather than a city, and its titles are often Hebrew.
    """
    import html
    jobs = read_json(opener(), ELBIT_JOBS.format(host=token), timeout=90)
    return [fetch_jobs.Job(
        company=company,
        title=" ".join((job.get("jobTitle") or "").split()),
        location=f"{job['area']}, Israel" if job.get("area") else "Israel",
        posted=(job.get("openDate") or "")[:10],
        url=f"https://{token}/jobs/?code={job.get('jobCode') or job.get('jobId')}",
        description=fetch_jobs.strip_html(html.unescape(" ".join(job.get(k) or "" for k in
                                                                 ("description", "requirements", "skills"))))
        if descriptions else "",
    ) for job in jobs if str(job.get("status")) == "1"]


def fetch_bob(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read a careers site hosted by Bob (HiBob), named by its subdomain, which the site also sends as a header."""
    session = opener()
    request = urllib.request.Request(f"https://{token}.careers.hibob.com/api/job-ad",
                                     headers={"User-Agent": AGENT, "Accept": "application/json",
                                              "companyIdentifier": token})
    with session.open(request, timeout=30) as response:
        ads = json.loads(response.read().decode("utf-8")).get("jobAdDetails") or []
    return [fetch_jobs.Job(
        company=company,
        title=ad.get("title", "").strip(),
        location=", ".join(p for p in (ad.get("site"), ad.get("country"), ad.get("workspaceType")) if p),
        posted=(ad.get("publishedAt") or "")[:10],
        url=f"https://{token}.careers.hibob.com/jobs/{ad.get('id', '')}",
        description=fetch_jobs.strip_html(" ".join(ad.get(k) or "" for k in
                                                   ("description", "responsibilities", "requirements")))
        if descriptions else "",
    ) for ad in ads if ad.get("country") == "Israel"]


ORACLE_SEARCH = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                 "&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},facetsList=LOCATIONS,"
                 "limit=25,offset={offset},sortBy=POSTING_DATES_DESC,locationId={place}")
ORACLE_DETAIL = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
                 "&onlyData=true&finder=ById;Id=%22{id}%22,siteNumber={site}")


def fetch_oracle(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read an Oracle Recruiting Cloud site, as Oracle and Dell use, for one location.

    The token is "host|site number|location id|site name"; the location id for Israel comes from the
    site's own location facet, since a keyword search for the country finds only jobs that mention it.
    """
    host, site, place, name = token.split("|")
    session, posts, offset = opener(), [], 0
    while offset < 1000:
        found = (read_json(session, ORACLE_SEARCH.format(host=host, site=site, offset=offset, place=place))
                 .get("items") or [{}])[0]
        posts += found.get("requisitionList") or []
        offset += 25
        if offset >= found.get("TotalJobsCount", 0) or not found.get("requisitionList"):
            break

    jobs = [fetch_jobs.Job(
        company=company,
        title=(post.get("Title") or "").strip(),
        location=post.get("PrimaryLocation") or "Israel",
        posted=(post.get("PostedDate") or "")[:10],
        url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{name}/job/{post.get('Id', '')}",
    ) for post in posts]

    if descriptions:
        known = known_descriptions(host)

        def describe(job: fetch_jobs.Job) -> fetch_jobs.Job:
            if job.url in known:
                job.description = known[job.url]
            elif workday.worth_reading(job.title):
                try:
                    number = job.url.rsplit("/", 1)[-1]
                    detail = (read_json(session, ORACLE_DETAIL.format(host=host, id=number, site=site))
                              .get("items") or [{}])[0]
                    job.description = fetch_jobs.strip_html(" ".join(
                        detail.get(k) or "" for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                      "ExternalQualificationsStr")))
                except Exception:
                    pass
            return job

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(describe, jobs))
    return jobs
