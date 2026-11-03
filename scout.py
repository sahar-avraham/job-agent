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
import devjobs
import fetch_jobs
import linkedin
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
    # Workable and DevJobs read only some of a company's jobs, so the map's other jobs there still count.
    names = [name for name, board, _ in fetch_jobs.COMPANIES if board not in ("techmap", "workable", "devjobs")]
    try:
        connection = store.connect()
        names += [row[0] for row in connection.execute("SELECT company FROM scout WHERE status = 'added'")]
        connection.close()
    except Exception:
        pass
    return names


def board_titles() -> dict[str, list[str]]:
    """The open jobs the boards gave in the latest collection, as titles by lowercase company name."""
    out: dict[str, list[str]] = {}
    try:
        connection = store.connect()
        latest = connection.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
        for company, title in connection.execute(
                "SELECT company, title FROM jobs WHERE last_seen = ? AND url NOT LIKE '%linkedin.com/jobs/view%'",
                (latest,)):
            out.setdefault(company.lower(), []).append(title)
        connection.close()
    except Exception:
        pass
    return out


def untracked(rows: list[dict]) -> set[str]:
    """The companies among these rows that no board reads.

    A name the boards use exactly counts as read. A name that only resembles one, such as Example and Example
    Technologies, two different companies, counts as read only when that board also lists one of the
    map's jobs there; otherwise the scout looks at the company's own careers page.
    """
    names = tracked_names()
    exact = {name.lower() for name in names}
    listed = board_titles()
    out = set()
    for company in {row["company"] for row in rows}:
        if company.lower() in exact:
            continue
        matched = [name for name in names if mail_sync.same_company(company, name)]
        titles = [row["title"] for row in rows if row["company"] == company]
        theirs = [title for name in matched for title in listed.get(name.lower(), [])]
        if not matched or (theirs and not any(mail_sync.title_similarity(a, b) >= 0.8 for a in titles for b in theirs)):
            out.add(company)
    return out


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
    # A company read by a board added another way, by hand or as a DevJobs page, shows as added with that board.
    boards = [(name, board, token) for name, board, token in fetch_jobs.COMPANIES if board not in ("techmap", "workable")]
    for row in connection.execute("SELECT company FROM scout WHERE status != 'added'").fetchall():
        match = next((b for b in boards if mail_sync.same_company(row[0], b[0])), None)
        if match:
            connection.execute("UPDATE scout SET status = 'added', system = ?, token = ?, note = '' WHERE company = ?",
                               (match[1], match[2], row[0]))
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

    A job that a company's DevJobs page already lists is left to that board. The rest come with a text
    that says only the title is known; `complete` looks for their full text once every board is read,
    and a job left without it is never scored (run.py), since a score from a title alone is a guess.
    The map's own date is the day of its list, so it is not taken as the posting's.
    """
    rows = map_jobs()
    missing = untracked(rows)
    covered: dict[str, set[str]] = {}
    for name, board, slug in fetch_jobs.COMPANIES:
        if board == "devjobs" and name in missing:
            try:
                covered.setdefault(name, set()).update(card[0] for card in devjobs.cards(slug))
            except Exception:
                pass
    jobs = []
    for row in rows:
        if row["company"] not in missing:
            continue
        if linkedin.number_of(row.get("url")) in covered.get(row["company"], set()):
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


# LinkedIn's job pages are read one at a time, and a refusal stops them until the next day's run.
LINKEDIN_PAUSE = (4.0, 12.0)
LINKEDIN_AT_MOST = 20
LINKEDIN_REST_HOURS = 20
UNPARSED_IN_A_ROW = 3


def same_job(job: dict, other: dict) -> bool:
    if (mail_sync.same_company(job["company"], other["company"])
            and mail_sync.title_similarity(job["title"], other["title"]) >= 0.8):
        return True
    # Compare a title's leading name with the company, since a fund or group that hires for its companies
    # lists "Example- Platform Engineer" for the job the map gives Example Data as "Platform Engineer".
    lead = re.match(r"\s*([^-–|:]+?)\s*[-–|:]\s*(.+)$", other["title"])
    return bool(lead and mail_sync.same_company(job["company"], lead.group(1))
                and mail_sync.title_similarity(job["title"], lead.group(2)) >= 0.8)


def leads_with(title: str, company: str) -> bool:
    lead = re.match(r"\s*([^-–|:]+?)\s*[-–|:]", title or "")
    return bool(lead) and mail_sync.same_company(company, lead.group(1))


def duplicates(map_rows: list[dict], direct: list[dict]) -> set[str]:
    """Links of map jobs that a board already gave: the same company by its words and a near-identical title."""
    by_company: dict[str, list[dict]] = {}
    for job in direct:
        by_company.setdefault(job["company"], []).append(job)
    twins: dict[str, list[dict]] = {}
    found = set()
    for job in map_rows:
        if job["company"] not in twins:
            # The same company's jobs, and any job whose title starts with this company's name.
            twins[job["company"]] = [other for name, jobs in by_company.items() for other in jobs
                                     if mail_sync.same_company(job["company"], name) or leads_with(other["title"], job["company"])]
        if any(same_job(job, other) for other in twins[job["company"]]):
            found.add(job["url"])
    return found


def rest(connection, why: str) -> None:
    """Stop reading LinkedIn for a day, and say why on the sources page."""
    until = (datetime.now(timezone.utc) + timedelta(hours=LINKEDIN_REST_HOURS)).isoformat(timespec="seconds")
    store.set_meta(connection, "linkedin_rest_until", until)
    store.set_meta(connection, "linkedin_stopped", f"{store.now()} {why}")


# LinkedIn's search runs after the map's pages, so a refusal it meets costs the map nothing. A job it
# lists is offered for SEARCH_KEEP_DAYS from its posting, since the search shows a job only in its
# first week, and a job whose text was read is asked about again after RECHECK_PAGE_DAYS, so a closed
# one leaves the page before then.
SEARCH_PAGES = 2
SEARCH_KEEP_DAYS = 30
RECHECK_PAGE_DAYS = 7
SEARCH_NOTE = f"{MAP_NOTE}, from LinkedIn's job search, with no posting text."


def search_linkedin(connection, pause, asked_before: int = 0) -> dict:
    """Ask LinkedIn's search for every query, keep what it lists, and say how it went."""
    cards: dict[str, dict] = {}
    asked, errors, stopped = 0, 0, ""
    for query in linkedin.QUERIES:
        for page in range(SEARCH_PAGES):
            if asked or asked_before:
                pause()
            asked += 1
            try:
                found = linkedin.search(query, page * linkedin.PAGE_SIZE)
            except linkedin.Refused as refusal:
                stopped = str(refusal)
                break
            except Exception:
                errors += 1
                break
            for card in found:
                cards.setdefault(linkedin.number_of(card["url"]), {**card, "query": query})
            if len(found) < linkedin.PAGE_SIZE:
                break
        if stopped:
            break
    if stopped:
        rest(connection, stopped)
    first = store.remember_found(connection, list(cards.values()))
    return {"asked": asked, "errors": errors, "listed": len(cards), "first_listed": first, "stopped": stopped}


def searched_jobs(connection, map_rows: list[dict], direct: list[dict], gone: set[str]) -> tuple[list[dict], Counter, int]:
    """The jobs LinkedIn's search listed in the past SEARCH_KEEP_DAYS, as jobs that wait for their text,
    the reasons the others were left out, and how many there were in all.

    A job at a company whose own board is read is left to that board, which lists all its jobs, and a
    job the map already gives is left to the map.
    """
    since = (datetime.now() - timedelta(days=SEARCH_KEEP_DAYS)).date().isoformat()
    rows = store.found_since(connection, since)
    in_map = {linkedin.number_of(j["url"]) for j in map_rows}
    names = sorted({name for name, board, _ in fetch_jobs.COMPANIES if board != "techmap"} | {j["company"] for j in direct})
    by_board: dict[str, bool] = {}
    left: Counter = Counter()
    jobs = []
    for row in rows:
        if row["company"] not in by_board:
            by_board[row["company"]] = any(mail_sync.same_company(row["company"], name) for name in names)
        if linkedin.number_of(row["url"]) in in_map:
            left["in the map"] += 1
        elif row["url"] in gone:
            left["closed"] += 1
        elif by_board[row["company"]]:
            left["own board"] += 1
        else:
            jobs.append({"company": row["company"], "title": row["title"], "location": row["location"] or "",
                         "posted": row["posted"] or "", "url": row["url"], "description": SEARCH_NOTE,
                         "source": "linkedin", "listed_at": row["last_seen"]})
    return jobs, left, len(rows)


def complete(jobs: list[dict], connection, read_linkedin: bool = True, stats: dict | None = None) -> list[dict]:
    """Give the map's jobs their full text once every board is read, add the jobs LinkedIn's search
    lists, and return the jobs to keep. With stats, also say there what each source gave and lost.

    A map or search job that a board already gave is dropped as a duplicate. For the rest whose title
    passes the hard rules and that are not sent or removed: the text found in an earlier run, else
    DevJobs by the LinkedIn number, else LinkedIn's own job page. A job LinkedIn shows as closed or gone
    is dropped. Every lookup is kept in map_texts, so no page is asked for twice.
    """
    import random
    import time

    stats = {} if stats is None else stats
    map_rows = [j for j in jobs if is_map_job(j)]
    direct = [j for j in jobs if not is_map_job(j) and "linkedin.com/jobs/view" not in (j.get("url") or "")]
    drop = duplicates(map_rows, direct)
    settled = store.settled_ids(connection)
    known = {row["url"]: dict(row) for row in connection.execute("SELECT * FROM map_texts")}
    stored = {row["url"]: (row["description"], row["posted"] or "") for row in connection.execute(
        "SELECT url, description, posted FROM jobs WHERE url LIKE '%linkedin.com/jobs/view%' AND description NOT LIKE ?",
        (MAP_NOTE + "%",))}
    gone = {url for url, row in known.items() if row["result"] in ("closed", "gone")}

    def note(url: str, source: str, result: str) -> None:
        connection.execute("INSERT OR REPLACE INTO map_texts (url, source, result, at) VALUES (?, ?, ?, ?)",
                           (url, source, result, store.now()))

    def reading() -> bool:
        return read_linkedin and store.now() >= (store.get_meta(connection, "linkedin_rest_until") or "")

    def pause() -> None:
        time.sleep(random.uniform(*LINKEDIN_PAUSE))

    def restore(rows: list[dict]) -> None:
        # Every job gets back the text found before, sent and removed ones too, or saving the run would
        # write the placeholder over the text they were scored and applied with.
        for job in rows:
            if job["url"] in stored:
                job["description"], job["posted"] = stored[job["url"]][0], stored[job["url"]][1] or job.get("posted", "")

    def worth(rows: list[dict], left_out: set[str]) -> list[dict]:
        return [j for j in rows if j["url"] not in left_out and j["url"] not in gone
                and store.job_id(j["url"]) not in settled and workday.worth_reading(j["title"])]

    # DevJobs first, a few at a time, since it is an open board.
    def from_devjobs(job: dict) -> tuple[dict, bool]:
        if not is_map_job(job):
            return job, False
        text, posted = devjobs.full_text(job["url"])
        if text:
            job["description"], job["posted"] = text, posted
        return job, bool(text)

    def from_devjobs_all(rows: list[dict]) -> None:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for job, found in pool.map(from_devjobs, rows):
                if found:
                    note(job["url"], "devjobs", "text")

    def from_linkedin(rows: list[dict], budget: int, asked_before: int) -> int:
        asked, unparsed = 0, 0
        for job in rows:
            # A page read before and found unreadable is not asked again; the job keeps waiting.
            if not is_map_job(job) or job["url"] in known or asked >= budget:
                continue
            if asked or asked_before:
                pause()
            asked += 1
            try:
                found = linkedin.read(linkedin.number_of(job["url"]))
            except linkedin.Refused as refusal:
                rest(connection, str(refusal))
                break
            except Exception:
                continue
            note(job["url"], "linkedin", found["result"])
            if found["result"] == "text":
                job["description"], job["posted"] = found["text"], found["posted"] or job.get("posted", "")
                unparsed = 0
            elif found["result"] in ("closed", "gone"):
                gone.add(job["url"])
            else:
                unparsed += 1
                if unparsed >= UNPARSED_IN_A_ROW:
                    rest(connection, f"page changed: no text on {UNPARSED_IN_A_ROW} pages in a row")
                    break
        return asked

    def recheck(rows: list[dict], budget: int, asked_before: int) -> int:
        """Ask again about search jobs read a while ago that today's search no longer lists."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RECHECK_PAGE_DAYS)).isoformat(timespec="seconds")
        today = store.now()[:10]
        due = [j for j in rows if not is_map_job(j) and store.job_id(j["url"]) not in settled
               and j["url"] in known and known[j["url"]]["at"] < cutoff and j["listed_at"][:10] < today]
        asked = 0
        for job in due[:max(budget, 0)]:
            if asked or asked_before:
                pause()
            asked += 1
            try:
                found = linkedin.read(linkedin.number_of(job["url"]))
            except linkedin.Refused as refusal:
                rest(connection, str(refusal))
                break
            except Exception:
                continue
            # An open page that cannot be parsed still says nothing against the job, so it counts as read.
            note(job["url"], "linkedin", "text" if found["result"] == "unparsed" else found["result"])
            if found["result"] in ("closed", "gone"):
                gone.add(job["url"])
        return asked

    restore(map_rows)
    for job in map_rows:
        job["source"] = "techmap"
    wanted = worth(map_rows, drop)
    from_devjobs_all(wanted)
    asked = from_linkedin(wanted, LINKEDIN_AT_MOST, 0) if reading() else 0

    search = {"asked": 0, "errors": 0, "listed": 0, "first_listed": 0, "stopped": ""}
    if not read_linkedin:
        search["stopped"] = "turned off for this run"
    elif not reading():
        search["stopped"] = "resting after " + (store.get_meta(connection, "linkedin_stopped") or "").split(" ", 1)[-1]
    else:
        search.update(search_linkedin(connection, pause, asked))
    map_kept = [j for j in map_rows if j["url"] not in drop]
    found, left, offered = searched_jobs(connection, map_kept, direct, gone)
    found_drop = duplicates(found, direct + map_kept)
    restore(found)
    wanted = worth(found, found_drop)
    from_devjobs_all(wanted)
    if reading():
        asked += from_linkedin(wanted, LINKEDIN_AT_MOST - asked, asked + search["asked"])
    if reading():
        asked += recheck([j for j in found if j["url"] not in found_drop], LINKEDIN_AT_MOST - asked,
                         asked + search["asked"])
    store.set_meta(connection, "linkedin_last_read", f"{store.now()} {asked}")
    connection.commit()

    left["duplicate"] += len(found_drop)
    left["closed"] += sum(1 for j in found if j["url"] in gone and j["url"] not in found_drop)
    stats["techmap"] = {"listed": len(map_rows), "duplicate": len(drop),
                        "closed": sum(1 for j in map_kept if j["url"] in gone)}
    stats["linkedin"] = {**search, "offered": offered, "left": dict(left), "pages": asked}
    return ([j for j in jobs if j["url"] not in drop and j["url"] not in gone]
            + [j for j in found if j["url"] not in found_drop and j["url"] not in gone])


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
