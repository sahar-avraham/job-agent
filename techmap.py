"""Find Israeli companies' job boards in the Israeli Tech Map, and add the ones hiring here.

The map (github.com/mluggy/techmap, by Michael Lugassy, ODbL 1.0) lists about 8,000 active Israeli
companies, many with the id of their board on Comeet, Greenhouse or Lever, and a daily list of
their open jobs whose links name the board too. The other catalogues start from a system and ask
which of its boards publish in Israel; this one starts from Israeli companies, so it finds the ones
those catalogues never listed.

    python techmap.py            download the map, check each board it names that is not tracked yet
    python techmap.py --save     and add the ones with jobs in Israel to companies.json

Comeet boards go through comeet.py's own check, which reads a company's feed key from its hosted
page; the others are asked directly. The map is data under the ODbL: companies.json, built from it
in part, stays private.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import catalogue
import comeet
import fetch_jobs
import workday

REPOSITORY = "https://github.com/mluggy/techmap"
FOLDER = catalogue.CATALOGUE_DIR / "techmap"

# A board's id in a careers link or a job link, per system.
LINKS = {
    "comeet": re.compile(r"comeet\.com/jobs/([^/?#]+)/([0-9A-F]{2}\.[0-9A-F]{3})", re.I),
    "greenhouse": re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I),
    "lever": re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_.-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.I),
    "workday": re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", re.I),
}


def sync() -> None:
    """Fetch only the company files and the job lists, since the logos are most of the repository."""
    if (FOLDER / ".git").exists():
        # The map rewrites its history daily, so a pull finds nothing in common; the copy is reset to the latest.
        subprocess.run(["git", "-C", str(FOLDER), "fetch", "-q", "--depth", "1", "origin"], check=True)
        subprocess.run(["git", "-C", str(FOLDER), "reset", "-q", "--hard", "FETCH_HEAD"], check=True)
        return
    catalogue.CATALOGUE_DIR.mkdir(exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", REPOSITORY, str(FOLDER)],
                   check=True)
    subprocess.run(["git", "-C", str(FOLDER), "sparse-checkout", "set", "companies", "jobs"], check=True)


def boards() -> dict[str, dict[str, tuple[str, str]]]:
    """Every board the map names, per system, as token to (company name, Comeet slug or "")."""
    found: dict[str, dict[str, tuple[str, str]]] = {system: {} for system in LINKS}

    def note(link: str, name: str) -> None:
        for system, pattern in LINKS.items():
            match = pattern.search(link or "")
            if not match:
                continue
            if system == "comeet":
                found[system].setdefault(match.group(2).upper(), (name, match.group(1)))
            elif system == "workday":
                found[system].setdefault("|".join(match.groups()).lower(), (name, ""))
            else:
                found[system].setdefault(match.group(1).lower(), (name, ""))

    for path in (FOLDER / "companies").glob("*.json"):
        company = json.loads(path.read_text(encoding="utf-8"))
        if not company.get("isActive", True):
            continue
        name = company.get("name", "")
        if company.get("comeetId"):
            slug, _, uid = company["comeetId"].partition("/")
            found["comeet"].setdefault(uid.upper(), (name, slug))
        for key, system in (("greenhouseId", "greenhouse"), ("leverId", "lever")):
            if company.get(key):
                found[system].setdefault(company[key].lower(), (name, ""))
        note(company.get("careersUrl"), name)
    for path in (FOLDER / "jobs").glob("*.csv"):
        with path.open(encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                note(row.get("url"), row.get("company", ""))
    return found


def untracked(found: dict[str, dict[str, tuple[str, str]]]) -> dict[str, dict[str, tuple[str, str]]]:
    tracked: dict[str, set[str]] = {}
    for _, system, token in fetch_jobs.COMPANIES:
        tracked.setdefault(system, set()).add(token.split(":")[0].upper() if system == "comeet" else token.lower())
    return {system: {t: v for t, v in tokens.items() if t not in tracked.get(system, set())}
            for system, tokens in found.items()}


def check_others(found: dict[str, dict[str, tuple[str, str]]], places: list[str]) -> list[tuple[str, str, str, int]]:
    """Ask each Greenhouse, Lever, Ashby and Workday board for its jobs here, as (name, system, token, count)."""
    def one(item):
        system, token, name = item
        if system == "workday":
            _, count, _ = workday.count_israel(token)
            return name, system, token, count
        result = catalogue.inspect((system, token), places, 20.0)
        return (result["name"] if result and result.get("name") else name), system, token, (result or {}).get("local", 0)

    todo = [(s, t, v[0]) for s, tokens in found.items() if s != "comeet" for t, v in tokens.items()]
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(one, todo))


def main() -> int:
    parser = argparse.ArgumentParser(description="Add Israeli companies' boards found in the Israeli Tech Map.")
    parser.add_argument("--save", action="store_true", help="add the boards with jobs in Israel to companies.json")
    parser.add_argument("--no-sync", action="store_true", help="use the copy of the map already downloaded")
    args = parser.parse_args()
    fetch_jobs.use_utf8_output()

    if not args.no_sync:
        sync()
    new = untracked(boards())
    print("boards named in the map and not tracked yet: "
          + ", ".join(f"{system} {len(tokens)}" for system, tokens in new.items()))

    # Comeet boards join comeet.py's queue, whose check finds each company's feed key on its hosted page.
    pages = json.loads(comeet.PAGES_FILE.read_text(encoding="utf-8")) if comeet.PAGES_FILE.exists() else {}
    queued = 0
    for uid, (_, slug) in new["comeet"].items():
        if uid not in pages:
            pages[uid] = {"slug": slug, "uid": uid}
            queued += 1
    comeet.PAGES_FILE.write_text(json.dumps(pages, indent=1), encoding="utf-8")
    # A company checked before with no jobs here, or unreadable, is asked again, since the map lists it now.
    if comeet.RESULT_FILE.exists():
        done = json.loads(comeet.RESULT_FILE.read_text(encoding="utf-8"))
        again = [uid for uid in new["comeet"] if uid in done and not done[uid].get("local")]
        for uid in again:
            del done[uid]
        comeet.RESULT_FILE.write_text(json.dumps(done, indent=1), encoding="utf-8")
        queued += len(again)
    print(f"\ncomeet: {queued} companies to check")
    comeet.sweep("israel", 8, 0)
    comeet.report(1, args.save)

    places = fetch_jobs.REGIONS["israel"]
    hiring = [r for r in check_others(new, places) if r[3]]
    print(f"\nother systems: {len(hiring)} of {sum(len(t) for s, t in new.items() if s != 'comeet')} boards have jobs in Israel")
    for name, system, token, count in sorted(hiring, key=lambda r: -r[3]):
        print(f"  {count:>4}  {name[:36]:<38} {system}:{token}")
    if hiring and args.save:
        added = fetch_jobs.add_companies([(name, system, token) for name, system, token, _ in hiring])
        print(f"added {added} to companies.json")
    elif hiring:
        print("run again with --save to add them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
