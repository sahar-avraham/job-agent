"""Fetch open positions from public ATS job boards and print them as one table.

Run with no arguments to see every position from the companies listed below, or
pass filters to narrow the list. Uses the standard library only, so no install
step is needed.
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
GREENHOUSE_URL_FULL = GREENHOUSE_URL + "?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
# A Lever board kept in Europe answers only on the EU host; its token is written "eu:name".
LEVER_EU_URL = "https://api.eu.lever.co/v0/postings/{token}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"
# Comeet needs two ids per company, kept in COMPANIES as one token written "uid:token".
COMEET_URL = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details={details}"

# Named regions, because many boards write only the city and never the country.
# Filtering on "israel" alone silently dropped well over a hundred Tel Aviv jobs.
REGIONS = {
    "israel": [
        "israel", "tel aviv", "tel-aviv", "telaviv", "herzliya", "herzelia", "haifa",
        "raanana", "ra'anana", "petah tikva", "petach tikva", "netanya", "jerusalem",
        "rehovot", "yokneam", "beer sheva", "be'er sheva", "ramat gan", "givatayim",
        "holon", "rishon", "caesarea", "hod hasharon", "kfar saba", "modiin",
        "airport city", "or yehuda", "bnei brak", "yakum", "glil yam",
    ],
}

# The companies to read live in companies.json, which is private and built by catalogue.py and
# comeet.py. It is kept out of the code and out of git on purpose: Comeet has no public
# directory of its customers, and each company's token, though visible on its own careers
# page, belongs to that company, so a collected list of them is not ours to publish.
# companies.example.json shows the shape: [name, board, token], and for Comeet "uid:token".
COMPANIES_FILE = pathlib.Path(__file__).resolve().parent / "companies.json"
EXAMPLE_FILE = COMPANIES_FILE.with_name("companies.example.json")


def load_companies() -> list[tuple[str, str, str]]:
    """Read the private list, or the example with a warning, so a fresh copy still runs."""
    path = COMPANIES_FILE if COMPANIES_FILE.exists() else EXAMPLE_FILE
    if path is EXAMPLE_FILE:
        print("companies.json not found, reading the example list. Build yours with catalogue.py and comeet.py.",
              file=sys.stderr)
    return [tuple(row) for row in json.loads(path.read_text(encoding="utf-8"))]


def add_companies(entries: list[tuple[str, str, str]]) -> int:
    """Append new companies to companies.json, skipping any already listed, and return how many were added."""
    current = [tuple(row) for row in json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))]         if COMPANIES_FILE.exists() else []
    known = {(board, token) for _, board, token in current}
    fresh = [tuple(e) for e in entries if (e[1], e[2]) not in known]
    COMPANIES_FILE.write_text(json.dumps([list(c) for c in current + fresh], ensure_ascii=False, indent=1),
                              encoding="utf-8")
    return len(fresh)


COMPANIES: list[tuple[str, str, str]] = load_companies()


@dataclass
class Job:
    """Holds the few fields both job boards agree on so the table stays uniform."""

    company: str
    title: str
    location: str
    posted: str
    url: str
    description: str = ""


def fetch_json(url: str, timeout: float = 20.0):
    """Fetch and decode one JSON document, sending a User-Agent because some boards reject blank ones."""
    request = urllib.request.Request(url, headers={"User-Agent": "job-agent/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def strip_html(text: str) -> str:
    """Turn a job description into plain text, because Greenhouse returns it as escaped HTML."""
    if not text:
        return ""
    # Unescape until it stops changing, since the payload is escaped twice over.
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    plain = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", plain.replace(" ", " ")).strip()


def iso_day(value) -> str:
    """Reduce the two different timestamp formats to a plain date for display."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return ""


def fetch_greenhouse(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read a Greenhouse board, which publishes its jobs under a single 'jobs' key."""
    url = GREENHOUSE_URL_FULL if descriptions else GREENHOUSE_URL
    payload = fetch_json(url.format(token=token))
    return [
        Job(
            company=company,
            title=job.get("title", ""),
            location=(job.get("location") or {}).get("name", ""),
            posted=iso_day(job.get("first_published") or job.get("updated_at")),
            url=job.get("absolute_url", ""),
            description=strip_html(job.get("content", "")) if descriptions else "",
        )
        for job in payload.get("jobs", [])
    ]


def lever_location(job: dict) -> str:
    """Every place a Lever posting names, which sit under its categories, plus its country when no place says it."""
    categories = job.get("categories") or {}
    places = categories.get("allLocations") or [categories.get("location") or ""]
    where = ", ".join(p for p in places if p)
    if job.get("country") == "IL" and "israel" not in where.lower():
        where = f"{where} · Israel" if where else "Israel"
    return where


def fetch_lever(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read a Lever board, which returns a bare list and names the title field 'text'."""
    payload = fetch_json(LEVER_EU_URL.format(token=token[3:]) if token.startswith("eu:") else LEVER_URL.format(token=token))
    return [
        Job(
            company=company,
            title=job.get("text", ""),
            location=lever_location(job),
            posted=iso_day(job.get("createdAt")),
            url=job.get("hostedUrl", ""),
            description=lever_description(job) if descriptions else "",
        )
        for job in payload
    ]


def lever_description(job: dict) -> str:
    """The whole posting: Lever keeps the requirements in separate lists, apart from the description,
    and the description alone left out "5+ years" and the like, which the filter and the scorer need."""
    parts = [job.get("descriptionPlain") or ""]
    for section in job.get("lists") or []:
        parts.append(f"{section.get('text', '')}\n{strip_html(section.get('content', ''))}")
    parts.append(job.get("additionalPlain") or "")
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def fetch_ashby(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read an Ashby board, which nests its jobs under 'jobs' and ships plain text descriptions."""
    payload = fetch_json(ASHBY_URL.format(token=token))
    return [
        Job(
            company=company,
            title=job.get("title", ""),
            location=job.get("location", ""),
            posted=iso_day(job.get("publishedAt")),
            url=job.get("jobUrl", ""),
            description=job.get("descriptionPlain", "") if descriptions else "",
        )
        for job in payload.get("jobs", [])
        if job.get("isListed", True)
    ]


def comeet_location(job: dict) -> str:
    """Spell out the country, because Comeet gives only a code and the region filter matches on words."""
    where = job.get("location") or {}
    parts = [where.get("city"), where.get("name")]
    if where.get("country") == "IL":
        parts.append("Israel")
    if job.get("workplace_type"):
        parts.append(job["workplace_type"])
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen)


def fetch_comeet(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read a Comeet board, whose description arrives as a list of titled HTML sections."""
    uid, key = token.split(":", 1)
    payload = fetch_json(COMEET_URL.format(uid=uid, token=key, details=str(descriptions).lower()))
    return [
        Job(
            company=company,
            title=job.get("name", ""),
            location=comeet_location(job),
            posted=iso_day(job.get("time_updated")),
            # The hosted page, because a company's own link can carry a changing query string and the id is the link.
            url=job.get("url_comeet_hosted_page") or job.get("url_active_page") or "",
            description=" ".join(
                f"{section.get('name', '')}: {strip_html(section.get('value') or '')}"
                for section in job.get("details") or []
            ) if descriptions else "",
        )
        for job in payload
        if not job.get("is_internal")
    ]


def fetch_workday(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read a Workday site's jobs in Israel. The adapter lives in workday.py, imported when first needed."""
    import workday
    return workday.fetch(company, token, descriptions)


WORKABLE_SEARCH = "https://jobs.workable.com/api/v1/jobs?location={token}"


def fetch_workable(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read Workable's own job search for one country, which spans every company on Workable.

    Unlike the other boards this is one entry for all of Workable's customers, so each job takes its
    company's name from the posting. The search is the one jobs.workable.com runs, and it is not a
    documented API, so a change on their side shows as this entry failing on the sources page.
    """
    jobs, page = [], None
    for _ in range(50):
        url = WORKABLE_SEARCH.format(token=urllib.parse.quote(token))
        payload = fetch_json(url + (f"&pageToken={urllib.parse.quote(page)}" if page else ""))
        for post in payload.get("jobs", []):
            text = " ".join(post.get(k) or "" for k in ("description", "requirementsSection", "benefitsSection"))
            jobs.append(Job(
                company=(post.get("company") or {}).get("title") or company,
                title=post.get("title", ""),
                location="; ".join(post.get("locations") or []),
                posted=(post.get("created") or post.get("updated") or "")[:10],
                url=post.get("url", ""),
                description=strip_html(text) if descriptions else "",
            ))
        page = payload.get("nextPageToken")
        if not page or not payload.get("jobs"):
            break
    return jobs


def fetch_smartrecruiters(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read a SmartRecruiters company's jobs in Israel. The adapter lives in smartrecruiters.py."""
    import smartrecruiters
    return smartrecruiters.fetch(company, token, descriptions)


def fetch_amazon(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read Amazon's jobs in one country. The adapter lives in employers.py."""
    import employers
    return employers.fetch_amazon(company, token, descriptions)


def fetch_eightfold(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read an Eightfold careers site, such as Microsoft's. The adapter lives in employers.py."""
    import employers
    return employers.fetch_eightfold(company, token, descriptions)


def fetch_elbit(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read Elbit Systems' careers site. The adapter lives in employers.py."""
    import employers
    return employers.fetch_elbit(company, token, descriptions)


def fetch_bob(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read a careers site hosted by Bob. The adapter lives in employers.py."""
    import employers
    return employers.fetch_bob(company, token, descriptions)


def fetch_oracle(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read an Oracle Recruiting Cloud site, such as Dell's. The adapter lives in employers.py."""
    import employers
    return employers.fetch_oracle(company, token, descriptions)


def fetch_techmap(company: str, token: str, descriptions: bool = False) -> list[Job]:
    """Read the Tech Map's jobs at companies no other board reads. The adapter lives in scout.py."""
    import scout
    return scout.fetch(company, token, descriptions)


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "comeet": fetch_comeet,
            "workday": fetch_workday, "workable": fetch_workable, "smartrecruiters": fetch_smartrecruiters,
            "amazon": fetch_amazon, "eightfold": fetch_eightfold,
            "elbit": fetch_elbit, "bob": fetch_bob, "oracle": fetch_oracle,
            "techmap": fetch_techmap}


def fetch_company(entry: tuple[str, str, str], descriptions: bool = False) -> tuple[str, list[Job], str]:
    """Fetch one company and return the error as text instead of raising, so one dead board cannot stop the run."""
    company, board, token = entry
    try:
        return company, FETCHERS[board](company, token, descriptions), ""
    except urllib.error.HTTPError as error:
        return company, [], f"HTTP {error.code}"
    except Exception as error:
        return company, [], str(error)


def matches(job: Job, keywords: list[str], places: list[str]) -> bool:
    """Keep a job when its title contains any keyword and its location matches any place."""
    title = job.title.lower()
    if keywords and not any(word in title for word in keywords):
        return False
    if not places:
        return True
    where = job.location.lower()
    return any(place in where for place in places)


def truncate(text: str, width: int) -> str:
    """Shorten a cell so the table keeps its columns aligned on a normal terminal."""
    return text if len(text) <= width else text[: width - 2] + ".."


def print_table(jobs: list[Job]) -> None:
    """Print the jobs as fixed-width columns, newest first."""
    widths = (14, 52, 26, 10)
    header = ("COMPANY", "TITLE", "LOCATION", "UPDATED")
    line = "  ".join(name.ljust(width) for name, width in zip(header, widths))
    print(line)
    print("-" * len(line))
    for job in jobs:
        cells = (job.company, job.title, job.location, job.posted)
        print("  ".join(truncate(cell, width).ljust(width) for cell, width in zip(cells, widths)))


def probe(token: str) -> None:
    """Report which board a token belongs to, so new companies can be added without guesswork."""
    for board, url in (("greenhouse", GREENHOUSE_URL), ("lever", LEVER_URL)):
        try:
            payload = fetch_json(url.format(token=token))
            count = len(payload.get("jobs", [])) if isinstance(payload, dict) else len(payload)
            print(f"{board}: found, {count} positions")
        except urllib.error.HTTPError as error:
            print(f"{board}: no ({error.code})")
        except Exception as error:
            print(f"{board}: no ({error})")


def use_utf8_output() -> None:
    """Switch stdout to UTF-8 because the Windows console defaults to a codepage that cannot print accented job titles."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Fetch open positions from public ATS job boards.")
    parser.add_argument("--keyword", default="", help="comma separated words, matched against the job title")
    parser.add_argument("--location", default="", help="a region name such as israel, or any substring of the job location")
    parser.add_argument("--json", dest="json_path", help="also write every matching job, with its link, to this file")
    parser.add_argument("--descriptions", action="store_true", help="also collect the full job text, which the scoring step needs")
    parser.add_argument("--probe", help="check which board a company token belongs to, then exit")
    args = parser.parse_args()

    if args.probe:
        probe(args.probe)
        return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda entry: fetch_company(entry, args.descriptions), COMPANIES))

    jobs: list[Job] = []
    failures: list[str] = []
    for company, company_jobs, error in results:
        jobs.extend(company_jobs)
        if error:
            failures.append(f"{company} ({error})")

    # A company that moved between boards can appear on both, so keep the first sighting.
    seen: set[tuple[str, str, str]] = set()
    unique: list[Job] = []
    for job in jobs:
        key = (job.company.lower(), job.title.strip().lower(), job.location.strip().lower())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    duplicates = len(jobs) - len(unique)
    jobs = unique

    keywords = [word.strip().lower() for word in args.keyword.split(",") if word.strip()]
    wanted = args.location.strip().lower()
    # A named region expands to its cities; anything else stays a plain substring.
    places = REGIONS.get(wanted, [wanted] if wanted else [])
    jobs = [job for job in jobs if matches(job, keywords, places)]
    jobs.sort(key=lambda job: job.posted, reverse=True)

    print_table(jobs)
    print()
    print(f"{len(jobs)} positions from {len(COMPANIES) - len(failures)} of {len(COMPANIES)} boards")
    if duplicates:
        print(f"{duplicates} duplicate postings dropped, the same role listed more than once")
    if failures:
        print("could not read: " + ", ".join(failures), file=sys.stderr)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump([asdict(job) for job in jobs], handle, ensure_ascii=False, indent=2)
        print(f"wrote {args.json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
