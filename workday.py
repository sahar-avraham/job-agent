"""Workday career sites: find the ones hiring in Israel, and read their jobs.

Workday gives every employer its own site, and has no public directory of them. The public
catalogue the other boards came from also lists about 13,000 Workday sites, each written
"tenant|wdN|site". Each site answers a JSON search, the same one its own careers page uses,
and that search lists its locations with an id, so a site's jobs in Israel can be asked for
directly instead of reading every job it has worldwide.

    python workday.py --sweep     ask every catalogue site how many jobs it has in Israel
    python workday.py --save      add the sites that have some to companies.json

After --save, run.py collects them like every other board.

Python on this machine rejects some sites' certificates that the system itself trusts, so
requests go through truststore, which checks them against the Windows certificate store.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import fetch_jobs
from catalogue import CATALOGUE_DIR

CATALOGUE_URL = "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/workday_companies.json"
CATALOGUE_FILE = CATALOGUE_DIR / "workday_companies.json"
RESULT_FILE = CATALOGUE_DIR / "workday.json"
AGENT = {"User-Agent": "Mozilla/5.0 job-agent/0.1", "Accept": "application/json"}
PAGE = 20  # the most one search returns; asking for more returns nothing


def context() -> ssl.SSLContext:
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


CONTEXT = context()


def parts(token: str) -> tuple[str, str, str]:
    tenant, wd, site = token.split("|")
    return tenant, wd, site


def api(token: str) -> str:
    tenant, wd, site = parts(token)
    return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"


def request(url: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {**AGENT, **({"Content-Type": "application/json"} if body is not None else {})}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers),
                                timeout=timeout, context=CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def israel_filter(facets: list[dict]) -> tuple[dict[str, list[str]], int]:
    """The facet values that mean Israel, and how many jobs they cover.

    A country value named exactly "Israel" is used when the site has one; otherwise every
    location whose name mentions Israel or an Israeli city, since some sites list only cities.
    """
    # City names without the country's own name, because "Israel" alone also names places abroad,
    # such as Boston's Beth Israel hospitals; the country counts only where it leads or follows a comma.
    places = [p for p in fetch_jobs.REGIONS["israel"] if p != "israel"]
    country_in_name = re.compile(r"^israel\b|,\s*israel\b|^isr\b")
    country, cities = {}, {}

    def walk(items):
        for facet in items:
            for value in facet.get("values") or []:
                if "facetParameter" in value:
                    walk([value])
                    continue
                name = (value.get("descriptor") or "").lower()
                if name == "israel":
                    country.setdefault(facet["facetParameter"], []).append((value["id"], value.get("count", 0)))
                elif facet["facetParameter"] in ("locations", "locationCountry", "Location_Country") and \
                        (country_in_name.search(name) or any(re.search(rf"\b{re.escape(p)}\b", name) for p in places)):
                    cities.setdefault(facet["facetParameter"], []).append((value["id"], value.get("count", 0)))

    walk(facets)
    chosen = country or cities
    if not chosen:
        return {}, 0
    key = next(iter(chosen))
    return {key: [i for i, _ in chosen[key]]}, sum(c for _, c in chosen[key])


def count_israel(token: str) -> tuple[str, int, str]:
    """How many jobs a site has in Israel, from one small search; an error comes back as text."""
    try:
        found = request(api(token) + "/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=20)
        return token, israel_filter(found.get("facets") or [])[1], ""
    except urllib.error.HTTPError as error:
        return token, 0, f"HTTP {error.code}"
    except Exception as error:
        return token, 0, str(error)[:80]


def known_descriptions() -> dict[str, str]:
    """Descriptions already stored, so a job seen in an earlier run is not fetched again."""
    try:
        import store
        connection = sqlite3.connect(store.DB_PATH)
        rows = connection.execute("SELECT url, description FROM jobs WHERE url LIKE '%myworkdayjobs.com%'"
                                  " AND description IS NOT NULL AND description != ''").fetchall()
        connection.close()
        return dict(rows)
    except Exception:
        return {}


def worth_reading(title: str) -> bool:
    """Whether a title can survive the hard rules, so a description is fetched only when it matters."""
    try:
        import filter_jobs
        return filter_jobs.rejection_reason({"title": title, "company": "", "description": ""},
                                            filter_jobs.MAX_YEARS_REQUIRED) is None
    except Exception:
        return True


def fetch(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """Read a site's jobs in Israel. Descriptions only for new jobs whose title passes the hard rules."""
    tenant, wd, site = parts(token)
    base = api(token)
    first = request(base + "/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    applied, total = israel_filter(first.get("facets") or [])
    if not applied:
        return []

    postings, offset = [], 0
    while offset < min(total, 1000):
        page = request(base + "/jobs", {"appliedFacets": applied, "limit": PAGE, "offset": offset, "searchText": ""})
        batch = page.get("jobPostings") or []
        if not batch:
            break
        postings += batch
        offset += PAGE

    jobs = []
    for post in postings:
        where = post.get("locationsText") or ""
        path = post.get("externalPath", "")
        if "israel" not in where.lower():
            # "2 Locations" says nothing, and the search already limited these to Israel. The job's
            # address starts with its first location, such as Israel-Tel-Aviv, which the commute filter needs.
            first = re.search(r"/job/([^/]+)/", path)
            where = (first.group(1).replace("-", " ") if first else "Israel") + (f" · {where}" if where else "")
        jobs.append(fetch_jobs.Job(
            company=company,
            title=post.get("title", ""),
            location=where,
            posted="",
            url=f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{post.get('externalPath', '')}",
        ))

    if descriptions:
        known = known_descriptions()

        def describe(job):
            if job.url in known:
                job.description = known[job.url]
            elif worth_reading(job.title):
                try:
                    path = job.url.split(f"/{site}", 1)[1]
                    info = request(base + path).get("jobPostingInfo") or {}
                    job.description = fetch_jobs.strip_html(info.get("jobDescription") or "")
                    job.posted = (info.get("startDate") or "")[:10]
                except Exception:
                    pass
            return job

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(describe, jobs))
    return jobs


def company_name(token: str) -> str:
    """The site's own name. Workday's hiring organisation is a legal entity, such as "2100 NVIDIA USA"."""
    return parts(token)[0].replace("_", " ").replace("-", " ").title()


def sweep(workers: int) -> None:
    """Ask every catalogue site for its count in Israel, resuming from what was saved before."""
    CATALOGUE_DIR.mkdir(exist_ok=True)
    if not CATALOGUE_FILE.exists():
        CATALOGUE_FILE.write_bytes(urllib.request.urlopen(CATALOGUE_URL, timeout=60).read())
    tokens = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
    done: dict[str, dict] = json.loads(RESULT_FILE.read_text(encoding="utf-8")) if RESULT_FILE.exists() else {}
    todo = [t for t in tokens if t not in done]
    print(f"{len(tokens)} sites in the catalogue, {len(todo)} still to ask")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (token, count, error) in enumerate(pool.map(count_israel, todo), start=1):
            done[token] = {"israel": count, "error": error}
            if count:
                print(f"  {count:>4} in Israel  {token}", flush=True)
            if n % 500 == 0:
                RESULT_FILE.write_text(json.dumps(done, indent=0), encoding="utf-8")
                print(f"  ... {n}/{len(todo)}", flush=True)
    RESULT_FILE.write_text(json.dumps(done, indent=0), encoding="utf-8")
    hits = {t: v for t, v in done.items() if v["israel"]}
    print(f"\n{len(hits)} sites have jobs in Israel, {sum(v['israel'] for v in hits.values())} jobs in all")


INTERNAL_SITE = re.compile(r"internal|redeploy|contingent|employee|alumni", re.I)


def recheck() -> None:
    """Ask again the sites that had jobs in Israel or were turned away for asking too fast."""
    done = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    again = [t for t, v in done.items() if v["israel"] or v["error"] == "HTTP 429"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for token, count, error in pool.map(count_israel, again):
            done[token] = {"israel": count, "error": error}
    RESULT_FILE.write_text(json.dumps(done, indent=0), encoding="utf-8")
    hits = {t: v for t, v in done.items() if v["israel"]}
    print(f"{len(again)} asked again: {len(hits)} sites have jobs in Israel, {sum(v['israel'] for v in hits.values())} jobs")


def save(minimum: int) -> None:
    """Add every site with at least this many jobs in Israel to companies.json, under its employer's name.

    A site for a company's own staff, such as an internal or redeployment board, is left out.
    """
    done = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    hits = [t for t, v in done.items() if v["israel"] >= minimum and not INTERNAL_SITE.search(parts(t)[2])]
    added = fetch_jobs.add_companies([(company_name(t), "workday", t) for t in hits])
    print(f"{added} Workday sites added to companies.json, {len(hits) - added} were there already")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find Workday career sites that hire in Israel.")
    parser.add_argument("--sweep", action="store_true", help="count each catalogue site's jobs in Israel")
    parser.add_argument("--save", action="store_true", help="add the sites with jobs in Israel to companies.json")
    parser.add_argument("--recheck", action="store_true", help="ask again the sites found before and those rate limited")
    parser.add_argument("--minimum", type=int, default=1, help="fewest jobs in Israel for --save to add a site")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    fetch_jobs.use_utf8_output()
    if args.sweep:
        sweep(args.workers)
    if args.recheck:
        recheck()
    if args.save:
        save(args.minimum)
    if not (args.sweep or args.save or args.recheck):
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
