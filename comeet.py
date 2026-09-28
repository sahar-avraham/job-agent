"""Find Israeli companies that hire through Comeet, and record which publish locally.

Comeet is common in Israel and absent from the public catalogue the other boards
came from, so those companies were invisible. It also needs two ids per company, a
uid and a token, and neither can be guessed. Both are printed inside the company's
hosted careers page, though, so the work is finding those pages.

Common Crawl, a public web archive, indexes every page it has fetched. Asking its
index for comeet.com/jobs pages returns the address of every company page it saw,
across as many monthly crawls as you ask for. That list is the catalogue.

Three steps, each resumable and each saved to a file under catalogue/:

    python comeet.py --collect     addresses from Common Crawl, a few minutes
    python comeet.py --sweep       read each page's ids and count local jobs
    python comeet.py --save        add what was found to companies.json

A company whose own site embeds Comeet, and which the archive never saw, can be
added directly from any of its careers pages:

    python comeet.py --add https://www.example.co.il/careers/some-job/ --save
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import fetch_jobs
from catalogue import CATALOGUE_DIR

PAGES_FILE = CATALOGUE_DIR / "comeet_pages.json"
RESULT_FILE = CATALOGUE_DIR / "comeet.json"

# Plain HTTP, because Windows Python rejects the index's certificate chain; the index is public and
# yields only page addresses, while every Comeet request below stays on HTTPS.
CRAWL_LIST = "http://index.commoncrawl.org/collinfo.json"
HOSTED_PAGE = "https://www.comeet.com/jobs/{slug}/{uid}"
PAGE_ADDRESS = re.compile(r"comeet\.com/jobs/([^/?#\s\"]+)/([0-9A-F]{2}\.[0-9A-F]{3})\b", re.I)

# The hosted page writes company_uid, a company's own site writes companyUid.
UID_PATTERN = re.compile(r"company_?uid\"?\s*[:=]\s*\"([0-9A-F]{2}\.[0-9A-F]{3})\"", re.I)
TOKEN_PATTERN = re.compile(r"\"token\"\s*:\s*\"([0-9A-F]{16,})\"", re.I)
NAME_PATTERN = re.compile(r"\"company_name\"\s*:\s*\"([^\"]+)\"")

AGENT = {"User-Agent": "Mozilla/5.0 job-agent/0.1"}


def get(url: str, timeout: float = 30.0) -> str:
    request = urllib.request.Request(url, headers=AGENT)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def patient_get(url: str, attempts: int = 4) -> str:
    """Retry with a growing pause, because the Common Crawl index answers 503 when it is busy."""
    for attempt in range(attempts):
        try:
            return get(url, timeout=90.0)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return ""  # a crawl that saw no page under the prefix
            if attempt == attempts - 1:
                raise
        except Exception:
            if attempt == attempts - 1:
                raise
        time.sleep(10 * (attempt + 1))
    return ""


def collect(crawls: int) -> None:
    """Gather every company page address from the most recent crawls, adding to what is saved."""
    CATALOGUE_DIR.mkdir(exist_ok=True)
    pages: dict[str, dict] = json.loads(PAGES_FILE.read_text(encoding="utf-8")) if PAGES_FILE.exists() else {}
    before = len(pages)

    indexes = json.loads(get(CRAWL_LIST))[:crawls]
    for index in indexes:
        api = index["cdx-api"].replace("https://", "http://", 1)
        found = 0
        for host in ("www.comeet.com", "www.comeet.co"):
            query = f"{api}?url={host}/jobs/*&output=json&fl=url"
            try:
                count = json.loads(patient_get(query + "&showNumPages=true") or '{"pages": 0}')["pages"]
                for page in range(count):
                    for line in patient_get(f"{query}&page={page}").splitlines():
                        match = PAGE_ADDRESS.search(line)
                        if match:
                            uid = match.group(2).upper()
                            if uid not in pages:
                                pages[uid] = {"slug": match.group(1), "uid": uid}
                                found += 1
            except Exception as error:
                print(f"  {index['id']} {host}: skipped, {error}", file=sys.stderr)
            time.sleep(2)  # the index is shared and free, so ask gently
        print(f"  {index['id']}: {found} new companies, {len(pages)} in total")
        PAGES_FILE.write_text(json.dumps(pages, indent=1), encoding="utf-8")

    print(f"\n{len(pages) - before} companies added, {len(pages)} known")


def read_ids(text: str) -> tuple[str, str, str] | None:
    """Pull uid, token and name out of a careers page, or nothing when it does not embed Comeet."""
    uid, token = UID_PATTERN.search(text), TOKEN_PATTERN.search(text)
    if not uid or not token:
        return None
    name = NAME_PATTERN.search(text)
    return uid.group(1).upper(), token.group(1), name.group(1) if name else ""


def count_local(uid: str, token: str, places: list[str]) -> tuple[int, int, str]:
    """Ask the positions feed how many jobs there are in total and how many are local."""
    jobs = fetch_jobs.fetch_json(fetch_jobs.COMEET_URL.format(uid=uid, token=token, details="false"))
    local = 0
    for job in jobs:
        where = fetch_jobs.comeet_location(job).lower()
        if any(place in where for place in places):
            local += 1
    name = next((j.get("company_name") for j in jobs if j.get("company_name")), "")
    return len(jobs), local, name


def inspect(page_url: str, places: list[str], known_name: str = "") -> dict:
    """Read one page's ids and count its jobs, recording a failure instead of raising."""
    try:
        ids = read_ids(get(page_url))
    except Exception as error:
        return {"page": page_url, "error": str(error)[:80]}
    if ids is None:
        return {"page": page_url, "error": "no Comeet ids on the page"}
    uid, token, name = ids
    try:
        total, local, feed_name = count_local(uid, token, places)
    except Exception as error:
        return {"page": page_url, "uid": uid, "error": f"feed: {str(error)[:60]}"}
    return {"page": page_url, "uid": uid, "token": token, "name": feed_name or name or known_name,
            "total": total, "local": local}


def sweep(region: str, workers: int, limit: int) -> None:
    """Visit every collected page not yet checked, saving as it goes so a stop loses nothing."""
    if not PAGES_FILE.exists():
        print("no addresses yet, run with --collect first", file=sys.stderr)
        return
    places = fetch_jobs.REGIONS.get(region, [region])
    pages = json.loads(PAGES_FILE.read_text(encoding="utf-8"))
    done: dict[str, dict] = json.loads(RESULT_FILE.read_text(encoding="utf-8")) if RESULT_FILE.exists() else {}

    todo = [p for uid, p in pages.items() if uid not in done]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("every collected company has been checked")
        return

    print(f"checking {len(todo)} companies, {workers} at a time\n")
    hits = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        urls = [HOSTED_PAGE.format(slug=p["slug"], uid=p["uid"]) for p in todo]
        for index, (page, result) in enumerate(zip(todo, pool.map(lambda u: inspect(u, places), urls)), start=1):
            done[page["uid"]] = result
            if result.get("local"):
                hits += 1
                print(f"  {result['local']:>4} in {region}  {result['name'][:36]:<38} {page['slug']}")
            if index % 50 == 0:
                RESULT_FILE.write_text(json.dumps(done, indent=1), encoding="utf-8")
                print(f"  ... {index}/{len(todo)} checked, {hits} with local jobs", file=sys.stderr)

    RESULT_FILE.write_text(json.dumps(done, indent=1), encoding="utf-8")
    print(f"\nchecked {len(todo)}, {hits} publish in {region}")


def add(urls: list[str], region: str) -> None:
    """Record companies from their own careers pages, for the ones the archive never saw."""
    places = fetch_jobs.REGIONS.get(region, [region])
    CATALOGUE_DIR.mkdir(exist_ok=True)
    done: dict[str, dict] = json.loads(RESULT_FILE.read_text(encoding="utf-8")) if RESULT_FILE.exists() else {}
    for url in urls:
        result = inspect(url, places)
        if "error" in result:
            print(f"  {url}: {result['error']}")
            continue
        done[result["uid"]] = result
        print(f"  {result['name']}: {result['local']} in {region} of {result['total']}")
    RESULT_FILE.write_text(json.dumps(done, indent=1), encoding="utf-8")


def report(minimum: int, save: bool = False) -> None:
    """List the companies worth adding, and add them to companies.json when asked."""
    if not RESULT_FILE.exists():
        print("nothing checked yet, run with --collect and then --sweep", file=sys.stderr)
        return
    done = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    existing = {token for _, board, token in fetch_jobs.COMPANIES if board == "comeet"}
    live = [r for r in done.values() if r.get("local", 0) >= minimum and r.get("token")]
    fresh = [r for r in live if f"{r['uid']}:{r['token']}" not in existing]
    failed = sum(1 for r in done.values() if "error" in r)

    print(f"\n{len(done)} companies checked, {failed} unreadable, {len(live)} publish at least {minimum} locally,"
          f" {len(fresh)} of them are not in companies.json yet\n")
    for record in sorted(fresh, key=lambda r: -r["local"]):
        print(f"  {record['local']:>4} local of {record['total']:<5} {record['name'][:40]}")
    if fresh and save:
        added = fetch_jobs.add_companies([(r["name"], "comeet", f"{r['uid']}:{r['token']}") for r in fresh])
        print(f"\nadded {added} to companies.json")
    elif fresh:
        print("\nrun again with --save to add them to companies.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find companies that hire through Comeet in your region.")
    parser.add_argument("--collect", action="store_true", help="gather company page addresses from Common Crawl")
    parser.add_argument("--crawls", type=int, default=12, help="how many monthly crawls to ask, newest first")
    parser.add_argument("--sweep", action="store_true", help="read each collected page and count local jobs")
    parser.add_argument("--add", nargs="+", metavar="URL", help="careers pages of companies to add directly")
    parser.add_argument("--region", default="israel")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="check only this many, for a quick trial")
    parser.add_argument("--min-jobs", type=int, default=1)
    parser.add_argument("--save", action="store_true", help="add the new companies to companies.json")
    args = parser.parse_args()

    fetch_jobs.use_utf8_output()
    if args.collect:
        collect(args.crawls)
    if args.sweep:
        sweep(args.region, args.workers, args.limit)
    if args.add:
        add(args.add, args.region)
    report(args.min_jobs, args.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
