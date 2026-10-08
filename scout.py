"""Find the companies hiring in the candidate's field that no tracked board reads, and bring them in.

The Israeli Tech Map (see techmap.py) publishes a daily list of open jobs at Israeli companies, by
field. Most of its links go to LinkedIn, so the list is used to learn who is hiring, not as a board:

1. Its jobs in the wanted fields are grouped by company, and the companies already read are set aside.
2. Each remaining company's careers page, as the map gives it, is searched once for the marks of a
   hiring system. A system with a fetcher here, and jobs in Israel, adds the company to companies.json.
3. A company left unread, because its page names no system, names one with no fetcher, or refuses
   the request, keeps its jobs from the map itself: title, level, city and link, with no description.
   They come in as the "techmap" board, so run.py collects, filters and scores them like any other.

    python scout.py              check the companies not checked yet and print the table
    python scout.py --again      check every company again

Each check is kept in the scout table, so a page is read once, and again only after RECHECK_DAYS.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import comeet
import fetch_jobs
import mail_sync
import store
import techmap
import workday

# The map's job lists that hold the candidate's roles; the hard rules sort the titles inside them.
FIELDS = ["software", "frontend", "devops", "data-science", "security", "qa", "hardware"]
RECHECK_DAYS = 30
# How a job taken from the map's rows begins its text, so the page and the tailoring can tell it apart.
MAP_NOTE = "Known from a list of open jobs only"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# Hiring systems with a fetcher here, found by a link or script on the careers page; each gives the token.
READABLE = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(eu\.)?lever\.co/([a-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#\"'\s]+)", re.I)),
    ("workday", re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([a-z0-9_-]+)", re.I)),
    ("bob", re.compile(r"([a-z0-9-]+)\.careers\.hibob\.com", re.I)),
    ("comeet", comeet.PAGE_ADDRESS),
]
# Systems recognised but not read here, named on the sources page so the biggest gap is visible.
UNREAD = [("Oracle", "oraclecloud.com"), ("SuccessFactors", "successfactors"), ("Phenom", "phenompeople"),
          ("Avature", "avature"), ("iCIMS", "icims.com"), ("Taleo", "taleo.net"), ("Eightfold", "eightfold"),
          ("Workable", "apply.workable.com"), ("Breezy", "breezy.hr"), ("Teamtailor", "teamtailor"),
          ("Recruitee", "recruitee"), ("BambooHR", "bamboohr"), ("Jobvite", "jobvite"), ("Niloosoft", "niloosoft"),
          ("Personio", "personio")]
# Placeholder tokens that a page's code carries without naming a real board.
NOT_A_TOKEN = {"embed", "js", "jobs", "careers", "api", "v1", "search", "job", "www"}


def map_jobs() -> list[dict]:
    """The map's open jobs in the wanted fields, each with the field it was listed under."""
    rows = []
    for field in FIELDS:
        path = techmap.FOLDER / "jobs" / f"{field}.csv"
        if path.exists():
            with path.open(encoding="utf-8-sig") as handle:
                rows += [dict(row, field=field) for row in csv.DictReader(handle)]
    return rows


def tracked_names() -> list[str]:
    """Names of the companies a board reads, plus the map's names of those the scout matched to a board,
    since the map may call a company otherwise ("Landa Digital Printing" for "Landa Corporation")."""
    names = [name for name, board, _ in fetch_jobs.COMPANIES if board not in ("techmap", "workable")]
    try:
        connection = store.connect()
        names += [row[0] for row in connection.execute("SELECT company FROM scout WHERE status = 'added'")]
        connection.close()
    except Exception:
        pass
    return names


def untracked(rows: list[dict]) -> set[str]:
    """The companies among these rows that no board reads, matched by name the way mail is matched."""
    names = tracked_names()
    return {company for company in {row["company"] for row in rows}
            if not any(mail_sync.same_company(company, other) for other in names)}


def careers_pages() -> dict[str, str]:
    """Each company's careers page from the map, else its website, by name."""
    pages = {}
    for path in (techmap.FOLDER / "companies").glob("*.json"):
        company = json.loads(path.read_text(encoding="utf-8"))
        if company.get("isActive", True):
            pages[company.get("name", "")] = company.get("careersUrl") or company.get("websiteUrl") or ""
    return pages


def read_page(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=25, context=workday.CONTEXT) as response:
        return response.geturl(), response.read(3_000_000).decode("utf-8", "replace")


# Links on a careers page worth following when the page itself names no system: the job list is often
# one click away, or drawn by a script the page loads.
FOLLOW = re.compile(r"""(?:href|src)=["']([^"'#]+)["']""", re.I)
LIKELY = re.compile(r"job|career|position|opening|vacanc|apply|join|hiring|comeet|greenhouse|lever|workday|ashby|smartrecruiters|hibob", re.I)
FOLLOW_AT_MOST = 6


def find_system(text: str) -> tuple[str, str]:
    """The first readable system named in this text, as (system, token), or two empty strings."""
    for system, pattern in READABLE:
        for match in pattern.finditer(text):
            if system == "lever":
                token = ("eu:" if match.group(1) else "") + match.group(2)
            elif system == "workday":
                token = "|".join(match.groups())
            elif system == "comeet":
                # The hosted page carries the feed key that the positions feed needs.
                found = comeet.inspect(comeet.HOSTED_PAGE.format(slug=match.group(1), uid=match.group(2).upper()),
                                       fetch_jobs.REGIONS["israel"])
                if not found.get("token"):
                    continue
                token = f"{found['uid']}:{found['token']}"
            else:
                token = match.group(1)
            if token.split(":")[-1].lower() not in NOT_A_TOKEN:
                return system, token
    return "", ""


def identify(url: str) -> tuple[str, str, str]:
    """Name the hiring system behind a careers page, as (system, token, note); system is "" when none is read here."""
    if not url:
        return "", "", "no careers page in the map"
    try:
        final, text = read_page(url)
    except Exception as error:
        return "", "", f"page did not open: {str(error)[:60]}"
    seen = final + " " + text
    system, token = find_system(seen)
    if not system:
        # One step further: the page's own job links and scripts, which often hold the board.
        links = []
        for href in FOLLOW.findall(text):
            link = urllib.parse.urljoin(final, href)
            if link.startswith("http") and LIKELY.search(link) and link not in links and link != final                     and not re.search(r"\.(png|jpe?g|svg|gif|webp|css|pdf|ico|woff2?)(\?|$)", link, re.I):
                links.append(link)
        for link in links[:FOLLOW_AT_MOST]:
            system, token = find_system(link)
            if system:
                break
            try:
                _, more = read_page(link)
            except Exception:
                continue
            seen += " " + more
            system, token = find_system(link + " " + more)
            if system:
                break
    if system:
        return system, token, ""
    named = [name for name, mark in UNREAD if mark in seen.lower()]
    return "", "", f"uses {named[0]}" if named else "names no system read here"


def local_jobs(name: str, system: str, token: str) -> tuple[int, str]:
    _, jobs, error = fetch_jobs.fetch_company((name, system, token))
    places = fetch_jobs.REGIONS["israel"]
    return sum(1 for job in jobs if fetch_jobs.matches(job, [], places)), error


def check(connection, again: bool = False, workers: int = 6) -> list[dict]:
    """Check the companies hiring in the map that are not read yet, add the readable ones, and return the table."""
    rows = map_jobs()
    missing = untracked(rows)
    counts = Counter(row["company"] for row in rows if row["company"] in missing)
    pages = careers_pages()
    stamp = store.now()
    since = (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).isoformat(timespec="seconds")
    known = {row["company"]: dict(row) for row in connection.execute("SELECT * FROM scout")}
    todo = [name for name in counts if again or name not in known or known[name]["checked_at"] < since]

    def one(name: str) -> dict:
        page = pages.get(name, "")
        system, token, note = identify(page)
        status = "unread"
        if system:
            try:
                local, error = local_jobs(name, system, token)
            except Exception as error_:
                local, error = 0, str(error_)
            if error:
                note = f"{system} did not answer: {error[:60]}"
            elif local:
                status = "added"
            else:
                note = f"{system} lists no jobs in Israel"
        return {"company": name, "careers": page, "system": system, "token": token, "status": status, "note": note}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, todo))

    added = [(r["company"], r["system"], r["token"]) for r in results if r["status"] == "added"]
    if added:
        fetch_jobs.add_companies(added)
        fetch_jobs.COMPANIES[:] = fetch_jobs.load_companies()
    for r in results:
        connection.execute(
            "INSERT INTO scout (company, careers, system, token, status, note, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (company) DO UPDATE SET careers = excluded.careers, system = excluded.system,"
            " token = excluded.token, status = excluded.status, note = excluded.note, checked_at = excluded.checked_at",
            (r["company"], r["careers"], r["system"], r["token"], r["status"], r["note"], stamp))
    # The count of jobs is the map's today, for every company it lists, checked now or before, added ones too.
    everywhere = Counter(row["company"] for row in rows)
    connection.execute("UPDATE scout SET jobs = 0")
    connection.executemany("UPDATE scout SET jobs = ?, seen_at = ? WHERE company = ?",
                           [(n, stamp, name) for name, n in everywhere.items()])
    connection.commit()
    return [dict(row) for row in connection.execute("SELECT * FROM scout WHERE jobs > 0 ORDER BY jobs DESC")]


def is_map_job(job: dict) -> bool:
    return (job.get("description") or "").startswith(MAP_NOTE)


def fetch(company: str, token: str, descriptions: bool = False) -> list[fetch_jobs.Job]:
    """The map's jobs at companies that no other board reads, taken from the map's own rows.

    A row has no description, so the text says what is known instead, and the model sees it is thin.
    The map's date is the day of its list, not of the posting, so the posting date is left unknown.
    """
    rows = map_jobs()
    missing = untracked(rows)
    jobs = []
    for row in rows:
        if row["company"] not in missing:
            continue
        link = urllib.parse.urlsplit(row.get("url") or "")
        query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(link.query) if not k.startswith("utm_")])
        jobs.append(fetch_jobs.Job(
            company=row["company"],
            title=" ".join((row.get("title") or "").split()),
            location=f"{row.get('city') or ''}, Israel".lstrip(", "),
            posted="",
            url=urllib.parse.urlunsplit(link._replace(query=query)),
            description=(f"{MAP_NOTE}, with no posting text: level {row.get('level') or 'unknown'},"
                         f" field {row['field']}, company size {row.get('size') or 'unknown'}.") if descriptions else "",
        ))
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="Find companies hiring in the candidate's field that no board reads.")
    parser.add_argument("--again", action="store_true", help="check every company again, not only new ones")
    parser.add_argument("--no-sync", action="store_true", help="use the copy of the map already downloaded")
    args = parser.parse_args()
    fetch_jobs.use_utf8_output()
    if not args.no_sync:
        techmap.sync()
    connection = store.connect()
    table = check(connection, args.again)
    print(f"{len(table)} companies hiring in the map that no board read before this check\n")
    for row in table:
        what = f"{row['system']}:{row['token']}" if row["status"] == "added" else row["note"]
        print(f"  {row['jobs']:>4}  {row['company'][:34]:<36} {row['status']:<7} {what}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
