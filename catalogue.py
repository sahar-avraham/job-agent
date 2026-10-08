"""Sweep every known job board and record which ones publish in your region.

Guessing a company's board token from its name was the wrong approach. Public
catalogues of tokens already exist and are refreshed daily, so there is nothing to
guess: read the catalogue, ask each board what it publishes, and keep the answer.

The sweep is a one-time cost of roughly fifteen minutes. Its result is a file, so
every later run reads it instantly. Re-run it every few weeks to pick up boards
that opened since.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import fetch_jobs

CATALOGUE_DIR = pathlib.Path("catalogue")
RESULT_FILE = CATALOGUE_DIR / "boards.json"

# One public catalogue per board, refreshed daily by its maintainer.
SOURCES = {
    "greenhouse": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json",
    "ashby": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/ashby_companies.json",
    "lever": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/lever_companies.json",
}


def download(url: str, timeout: float = 90.0) -> list[str]:
    """Fetch one catalogue and return its tokens."""
    request = urllib.request.Request(url, headers={"User-Agent": "job-agent/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_catalogues(refresh: bool) -> dict[str, list[str]]:
    """Return every known token per board, downloading only when the cache is missing or stale."""
    CATALOGUE_DIR.mkdir(exist_ok=True)
    out: dict[str, list[str]] = {}
    for board, url in SOURCES.items():
        cached = CATALOGUE_DIR / f"{board}_tokens.json"
        if refresh or not cached.exists():
            print(f"downloading the {board} catalogue")
            tokens = download(url)
            cached.write_text(json.dumps(tokens), encoding="utf-8")
        else:
            tokens = json.loads(cached.read_text(encoding="utf-8"))
        out[board] = tokens
        print(f"  {board:<12}{len(tokens):>7} tokens")
    return out


def company_name(board: str, payload, token: str) -> str:
    """Take the company's real name from the payload when it carries one, else tidy the token."""
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    for job in jobs[:3]:
        for key in ("company_name", "companyName"):
            if isinstance(job, dict) and job.get(key):
                return job[key]
    return token.replace("-", " ").replace("_", " ").title()


def inspect(entry: tuple[str, str], places: list[str], timeout: float) -> dict | None:
    """Ask one board what it publishes, and report only what matters for the decision."""
    board, token = entry
    url = {"greenhouse": fetch_jobs.GREENHOUSE_URL,
           "ashby": fetch_jobs.ASHBY_URL,
           "lever": fetch_jobs.LEVER_URL}[board].format(token=token)

    request = urllib.request.Request(url, headers={"User-Agent": "job-agent/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        return None

    local = 0
    for job in jobs:
        # Lever keeps the place under categories, so its postings read as having none without this.
        where = fetch_jobs.lever_location(job) if board == "lever" else job.get("location")
        if isinstance(where, dict):
            where = where.get("name", "")
        where = str(where or "").lower()
        if any(place in where for place in places):
            local += 1

    return {
        "board": board,
        "token": token,
        "name": company_name(board, payload, token),
        "total": len(jobs),
        "local": local,
    }


def sweep(catalogues: dict[str, list[str]], region: str, workers: int, timeout: float, limit: int,
          again: bool = False) -> None:
    """Walk every token, saving as it goes so an interrupted sweep resumes where it stopped.

    With again, the earlier answers for these boards are dropped first, for a sweep whose check changed.
    """
    places = fetch_jobs.REGIONS.get(region, [region])
    done: dict[str, dict] = {}
    if RESULT_FILE.exists():
        done = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        if again:
            done = {k: v for k, v in done.items() if k.split(":", 1)[0] not in catalogues}
        print(f"resuming, {len(done)} boards already checked")

    todo = [
        (board, token)
        for board, tokens in catalogues.items()
        for token in tokens
        if f"{board}:{token}" not in done
    ]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("every token in the catalogue has been checked, pass --refresh for a newer one")
        return

    print(f"checking {len(todo)} boards, {workers} at a time. This takes a while.\n")
    started = time.monotonic()
    checked = hits = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for entry, result in zip(todo, pool.map(lambda e: inspect(e, places, timeout), todo)):
            checked += 1
            key = f"{entry[0]}:{entry[1]}"
            done[key] = result or {"board": entry[0], "token": entry[1], "total": None}

            if result and result["local"]:
                hits += 1
                print(f"  {result['local']:>4} in {region}  {result['name'][:34]:<36} {entry[0]}:{entry[1]}")

            if checked % 200 == 0:
                RESULT_FILE.write_text(json.dumps(done), encoding="utf-8")
                rate = checked / max(time.monotonic() - started, 1)
                left = (len(todo) - checked) / max(rate, 0.1) / 60
                print(f"  ... {checked}/{len(todo)} checked, {hits} with local jobs, about {left:.0f} minutes left",
                      file=sys.stderr)

    RESULT_FILE.write_text(json.dumps(done), encoding="utf-8")
    print(f"\nchecked {checked} boards, {hits} publish in {region}")


def report(minimum: int, existing: set[str], save: bool = False) -> None:
    """List the boards worth adding, and add them to companies.json when asked."""
    if not RESULT_FILE.exists():
        print("no sweep result yet, run with --sweep", file=sys.stderr)
        return

    done = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    live = [r for r in done.values() if r.get("local", 0) and r["local"] >= minimum]
    fresh = [r for r in live if r["token"] not in existing]

    print(f"\n{len(done)} boards checked, {len(live)} publish at least {minimum} locally,"
          f" {len(fresh)} of them are not in companies.json yet\n")
    for record in sorted(fresh, key=lambda r: -r["local"]):
        print(f"  {record['local']:>4} local of {record['total']:<5} {record['name'][:40]:<42} {record['board']}")
    if fresh and save:
        added = fetch_jobs.add_companies([(r["name"], r["board"], r["token"]) for r in fresh])
        print(f"\nadded {added} to companies.json")
    elif fresh:
        print("\nrun again with --save to add them to companies.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find every board that publishes in your region.")
    parser.add_argument("--region", default="israel", help="a region name from fetch_jobs.REGIONS")
    parser.add_argument("--sweep", action="store_true", help="check every token, the slow one-time pass")
    parser.add_argument("--refresh", action="store_true", help="download the catalogues again before sweeping")
    parser.add_argument("--workers", type=int, default=10, help="how many boards to check at once")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--limit", type=int, default=0, help="check only this many, for a quick trial")
    parser.add_argument("--min-jobs", type=int, default=1, help="ignore boards with fewer local jobs than this")
    parser.add_argument("--save", action="store_true", help="add the new boards to companies.json")
    parser.add_argument("--board", choices=sorted(SOURCES), help="sweep only this system's catalogue")
    parser.add_argument("--again", action="store_true", help="check the swept boards again, for a changed check")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.sweep or args.refresh:
        catalogues = load_catalogues(args.refresh)
        if args.board:
            catalogues = {args.board: catalogues[args.board]}
        if args.sweep:
            sweep(catalogues, args.region, args.workers, args.timeout, args.limit, args.again)

    already = {token for _, _, token in fetch_jobs.COMPANIES}
    report(args.min_jobs, already, args.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
