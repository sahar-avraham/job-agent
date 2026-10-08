"""SmartRecruiters boards: find the companies hiring in Israel, and read their jobs.

SmartRecruiters answers per company, and filters by country itself. There is no public list of its
companies, but its own job search, the one jobs.smartrecruiters.com runs, finds jobs by keyword, and
a search for an Israeli city returns jobs in Israel with their company's identifier. So the sweep
searches the cities, collects the companies, and asks each one how many jobs it has here.

    python smartrecruiters.py            list the companies found and their jobs in Israel
    python smartrecruiters.py --save     add the ones not tracked yet to companies.json

After --save, run.py collects them like every other board.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import fetch_jobs
import mail_sync

SEARCH = "https://jobs.smartrecruiters.com/sr-jobs/search?keyword={keyword}&limit=100&offset={offset}"
POSTINGS = "https://api.smartrecruiters.com/v1/companies/{company}/postings?country=il&limit=100&offset={offset}"
POSTING = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting}"
AGENT = {"User-Agent": "Mozilla/5.0 job-agent/0.1", "Accept": "application/json"}
# The keyword search reads the words of a job's place, so each city is searched on its own.
CITIES = ["Israel", "Tel Aviv", "Herzliya", "Haifa", "Petah Tikva", "Ra'anana", "Netanya", "Rehovot",
          "Jerusalem", "Kfar Saba", "Hod Hasharon", "Yokneam", "Ramat Gan", "Rosh HaAyin", "Or Yehuda",
          "Airport City", "Caesarea", "Beer Sheva", "Modiin", "Lod", "Holon", "Bnei Brak"]


def request(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def known_descriptions() -> dict[str, str]:
    """Descriptions already stored, so a job seen in an earlier run is not asked for again."""
    try:
        import store
        connection = sqlite3.connect(store.DB_PATH)
        rows = connection.execute("SELECT url, description FROM jobs WHERE url LIKE '%jobs.smartrecruiters.com%'"
                                  " AND description IS NOT NULL AND description != ''").fetchall()
        connection.close()
        return dict(rows)
    except Exception:
        return {}


def fetch(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read one company's jobs in Israel. Descriptions only for new jobs whose title passes the hard rules."""
    import workday  # for worth_reading, the same test of a title against the hard rules

    posts, offset = [], 0
    while True:
        page = request(POSTINGS.format(company=urllib.parse.quote(token), offset=offset))
        posts += page.get("content") or []
        offset += 100
        if offset >= page.get("totalFound", 0) or not page.get("content"):
            break

    jobs = [fetch_jobs.Job(
        company=company,
        title=post.get("name", ""),
        location=(post.get("location") or {}).get("fullLocation", "").replace(", ,", ",") or "Israel",
        posted=(post.get("releasedDate") or "")[:10],
        url=f"https://jobs.smartrecruiters.com/{token}/{post.get('id', '')}",
    ) for post in posts]

    if descriptions:
        known = known_descriptions()

        def describe(pair):
            job, post = pair
            if job.url in known:
                job.description = known[job.url]
            elif workday.worth_reading(job.title):
                try:
                    sections = (request(POSTING.format(company=urllib.parse.quote(token), posting=post["id"]))
                                .get("jobAd") or {}).get("sections") or {}
                    job.description = fetch_jobs.strip_html(" ".join((s or {}).get("text") or "" for s in sections.values()))
                except Exception:
                    pass
            return job

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(describe, zip(jobs, posts)))
    return jobs


def sweep() -> dict[str, tuple[str, int]]:
    """Every company the city searches find with a job in Israel, as identifier to (name, jobs here)."""
    found: dict[str, str] = {}
    for city in CITIES:
        offset = 0
        while offset < 1000:
            page = request(SEARCH.format(keyword=urllib.parse.quote(city), offset=offset))
            for job in page.get("content") or []:
                if (job.get("location") or {}).get("country") == "il":
                    found.setdefault(job["company"]["identifier"], job["company"]["name"])
            offset += 100
            if offset >= page.get("totalFound", 0):
                break

    def count(item):
        identifier, name = item
        try:
            return identifier, name, request(POSTINGS.format(company=urllib.parse.quote(identifier), offset=0)).get("totalFound", 0)
        except Exception:
            return identifier, name, 0

    with ThreadPoolExecutor(max_workers=6) as pool:
        return {identifier: (name, n) for identifier, name, n in pool.map(count, found.items())}


def main() -> int:
    parser = argparse.ArgumentParser(description="Find SmartRecruiters companies hiring in Israel.")
    parser.add_argument("--save", action="store_true", help="add the companies not tracked yet to companies.json")
    parser.add_argument("--skip", nargs="*", default=[], help="identifiers to leave out, such as ones not in tech")
    args = parser.parse_args()
    fetch_jobs.use_utf8_output()

    tracked = {token for _, board, token in fetch_jobs.COMPANIES if board == "smartrecruiters"}
    names = [name for name, _, _ in fetch_jobs.COMPANIES]

    def elsewhere(name: str) -> bool:
        # Matched by words, so "Kaltura, Inc." here is the Kaltura already read from Comeet.
        return any(mail_sync.same_company(name, other) for other in names)
    found = sweep()
    fresh = [(identifier, name, n) for identifier, (name, n) in found.items()
             if n and identifier not in tracked and identifier not in args.skip]
    print(f"{len(found)} companies found with jobs in Israel, {len(fresh)} not tracked yet\n")
    for identifier, name, n in sorted(fresh, key=lambda r: -r[2]):
        note = "  (tracked on another board)" if elsewhere(name) else ""
        print(f"  {n:>4}  {name[:40]:<42} {identifier}{note}")
    if fresh and args.save:
        added = fetch_jobs.add_companies([(name, "smartrecruiters", identifier) for identifier, name, _ in fresh
                                          if not elsewhere(name)])
        print(f"\nadded {added} to companies.json")
    elif fresh:
        print("\nrun again with --save to add them; a company tracked on another board is not added twice")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
