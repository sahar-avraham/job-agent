"""Find one company's board token when the catalogue does not have it.

This is the fallback, not the main route. catalogue.py sweeps every token that is
publicly listed and is both faster and far more reliable, so a company should be
looked for there first. This script exists for the case that catalogue leaves
open: a company that opened a board after the catalogue was last published.

It therefore reports where each answer came from. A company found only by guessing
is a sign the catalogue has fallen behind, and refreshing it is usually the better
fix than keeping the guess.

Give it names on the command line, or a file with one name per line.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"

CATALOGUE_RESULT = pathlib.Path("catalogue") / "boards.json"


def load_catalogue() -> list[dict]:
    """Read what the sweep already found, so a known company is never guessed at."""
    if not CATALOGUE_RESULT.exists():
        return []
    records = json.loads(CATALOGUE_RESULT.read_text(encoding="utf-8"))
    return [r for r in records.values() if r.get("total") is not None]


def from_catalogue(name: str, catalogue: list[dict]) -> tuple[str, str, str, int] | None:
    """Look the company up by name, then by token, before any guessing happens."""
    wanted = re.sub(r"[^a-z0-9]+", "", name.lower())
    if not wanted:
        return None
    for record in catalogue:
        listed = re.sub(r"[^a-z0-9]+", "", str(record.get("name", "")).lower())
        token = re.sub(r"[^a-z0-9]+", "", record["token"].lower())
        if wanted and (wanted == listed or wanted == token):
            return name, record["board"], record["token"], record.get("local") or record["total"]
    return None


def candidates(name: str) -> list[str]:
    """Derive the token spellings a company is most likely to have registered."""
    base = name.strip().lower()
    plain = re.sub(r"[^a-z0-9]+", "", base)
    hyphen = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    # Many companies register the name without its legal or descriptive suffix.
    trimmed = re.sub(r"(technologies|technology|labs|software|systems|security|group|inc|ltd)$", "", plain)

    # Companies often register the name plus their domain ending or legal suffix,
    # so gong.io is "gongio" and Wiz is "wizinc".
    suffixed = [plain + suffix for suffix in ("inc", "io", "ai", "com", "hq", "global", "us", "labs")]

    seen, out = set(), []
    for token in [plain, hyphen, trimmed, *suffixed]:
        if token and len(token) > 1 and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def count_jobs(url: str, timeout: float = 12.0) -> int | None:
    """Return how many positions a board publishes, or None when the token does not exist."""
    request = urllib.request.Request(url, headers={"User-Agent": "job-agent/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    if isinstance(payload, dict):
        return len(payload.get("jobs", []))
    return len(payload)


def find(name: str) -> tuple[str, str, str, int] | None:
    """Try every candidate token on every board and return the board publishing the most jobs.

    Returning the first board that answers is wrong: a company that moved systems
    often leaves an empty board behind on the old one, and an empty board would
    otherwise hide the live one.
    """
    matches: list[tuple[str, str, str, int]] = []
    for token in candidates(name):
        for board, url in (("greenhouse", GREENHOUSE_URL), ("ashby", ASHBY_URL), ("lever", LEVER_URL)):
            count = count_jobs(url.format(token=token))
            if count is not None:
                matches.append((name, board, token, count))
    if not matches:
        return None
    return max(matches, key=lambda match: match[3])


def main() -> int:
    parser = argparse.ArgumentParser(description="Find the job board token for a list of companies.")
    parser.add_argument("names", nargs="*", help="company names, quoted if they contain spaces")
    parser.add_argument("--file", help="a file with one company name per line")
    parser.add_argument("--min-jobs", type=int, default=1, help="ignore boards publishing fewer positions than this")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    names = list(args.names)
    if args.file:
        path = pathlib.Path(args.file)
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        names += [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]

    if not names:
        parser.error("give at least one company name, or --file")

    catalogue = load_catalogue()
    print(f"catalogue holds {len(catalogue)} boards" if catalogue
          else "no catalogue on disk, every name will be guessed. Run catalogue.py --sweep first.")

    known = {name: from_catalogue(name, catalogue) for name in names}
    unknown = [name for name, hit in known.items() if hit is None]

    # Six at a time keeps the probing polite while still finishing quickly.
    guessed: dict[str, tuple | None] = {}
    if unknown:
        print(f"{len(unknown)} of {len(names)} are not in the catalogue, guessing those")
        with ThreadPoolExecutor(max_workers=6) as pool:
            guessed = dict(zip(unknown, pool.map(find, unknown)))

    results = [known[name] or guessed.get(name) for name in names]
    sources = {name: ("catalogue" if known[name] else "guessed") for name in names}

    found = [r for r in results if r and r[3] >= args.min_jobs]
    empty = [r for r in results if r and r[3] < args.min_jobs]
    missing = [name for name, result in zip(names, results) if result is None]

    # A hit that only guessing found is evidence the catalogue needs refreshing.
    stale = [r[0] for r in found if sources.get(r[0]) == "guessed"]

    print(f"checked {len(names)} companies, matched {len(found)}\n")
    print("add these to companies.json, as [name, board, token]:\n")
    for name, board, token, count in sorted(found, key=lambda r: -r[3]):
        origin = sources.get(name, "guessed")
        print(f'    ("{name}", "{board}", "{token}"),'.ljust(56) + f"# {count} positions, {origin}")

    if stale:
        print("\nfound only by guessing, which means the catalogue is behind:")
        print("  " + ", ".join(stale))
        print("  refresh it with: python catalogue.py --sweep --refresh")

    if empty:
        print("\nboard found but publishing nothing right now:")
        for name, board, token, _ in empty:
            print(f"  {name} ({board}: {token})")
    if missing:
        print("\nno board found, they may use another system or a different token:")
        print("  " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
