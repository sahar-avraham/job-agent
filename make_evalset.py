"""Build the evaluation set: a sample of collected jobs for you to score by hand.

The scores you write here are the only yardstick the project has. Every later
change to the rubric or the prompt is measured against them, so without this file
you cannot tell an improvement from a regression.

Writes two files. The spreadsheet is where you type scores, and the reading copy
holds the full job text so you can judge each one properly. They are joined by a
short id, which stays the same as long as the job keeps its link.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import random
import sys
from collections import Counter, defaultdict

import filter_jobs

COLUMNS = ["id", "company", "title", "location", "hard_filter", "my_score", "my_decision", "notes", "url"]

INSTRUCTIONS = """# Evaluation set

Score every job below, then save the spreadsheet. Read the description before
scoring, because the title alone is misleading more often than not.

- my_score is 0 to 100, by the rubric in profile.md.
- my_decision is one of apply, review, skip.
- notes is optional, but one line on a borderline job is worth a lot later.

Score the job you would actually want, not the job you think you can get. The
threshold is tuned separately, and mixing the two questions makes both answers
useless.

The hard_filter column shows what filter_jobs.py decided without any model. A job
marked dropped that you score highly means the hard rules are too strict, and
that is worth knowing on its own.
"""


def job_id(job: dict) -> str:
    """Derive a short stable id from the link, so the two files stay joined across regenerations."""
    return hashlib.sha1(job.get("url", "").encode("utf-8")).hexdigest()[:8]


def spread(jobs: list[dict], size: int, seed: int) -> list[dict]:
    """Take a spread across companies, so one large board cannot dominate the set."""
    by_company: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        by_company[job.get("company", "")].append(job)

    rng = random.Random(seed)
    for group in by_company.values():
        rng.shuffle(group)

    picked: list[dict] = []
    companies = sorted(by_company)
    while len(picked) < size:
        took_any = False
        for company in companies:
            if by_company[company]:
                picked.append(by_company[company].pop())
                took_any = True
                if len(picked) >= size:
                    break
        if not took_any:
            break
    return picked


def sample(jobs: list[dict], size: int, seed: int, keep_ratio: float, max_years: int) -> tuple[list[dict], int]:
    """Split the pool by what the hard filter decided, then fill the set to a target mix.

    A set made only of survivors cannot show that the hard rules are too strict, and
    a set made mostly of rejects wastes your time scoring jobs the pipeline never sees.
    """
    kept = [job for job in jobs if filter_jobs.rejection_reason(job, max_years) is None]
    dropped = [job for job in jobs if filter_jobs.rejection_reason(job, max_years) is not None]

    want_kept = min(len(kept), round(size * keep_ratio))
    picked = spread(kept, want_kept, seed)
    picked += spread(dropped, size - len(picked), seed)
    return picked, len(kept)


def write_sheet(path: pathlib.Path, rows: list[dict]) -> None:
    """Write the spreadsheet with a BOM, because Excel misreads UTF-8 without one."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_reading_copy(path: pathlib.Path, jobs: list[dict], verdicts: dict[str, str]) -> None:
    """Write the full text of every sampled job, which is what you actually score against."""
    parts = [INSTRUCTIONS]
    for job in jobs:
        identifier = job_id(job)
        title = job.get("title", "")
        company = job.get("company", "")
        location = job.get("location", "")
        url = job.get("url", "")
        body = job.get("description") or "(no description was collected for this job)"
        parts.append(
            "\n---\n\n"
            f"## {identifier} - {title}\n\n"
            f"**{company}** - {location} - hard filter: {verdicts[identifier]}\n\n"
            f"{url}\n\n"
            f"{body}\n"
        )
    path.write_text("\n".join(parts), encoding="utf-8")


def build(args) -> int:
    source = pathlib.Path(args.jobs)
    if not source.exists():
        print(f"missing {source}, run: python fetch_jobs.py --descriptions --json {source}", file=sys.stderr)
        return 1

    jobs = json.loads(source.read_text(encoding="utf-8"))
    if not jobs:
        print(f"{source} is empty", file=sys.stderr)
        return 1

    sheet = pathlib.Path(args.out)
    if sheet.exists() and not args.force:
        print(f"{sheet} already exists, refusing to overwrite scores, pass --force to replace it", file=sys.stderr)
        return 1

    picked, kept_available = sample(jobs, args.size, args.seed, args.keep_ratio, args.max_years)
    verdicts: dict[str, str] = {}
    rows = []
    for job in picked:
        identifier = job_id(job)
        reason = filter_jobs.rejection_reason(job, args.max_years)
        verdicts[identifier] = f"dropped: {reason}" if reason else "kept"
        rows.append(
            {
                "id": identifier,
                "company": job.get("company", ""),
                "title": job.get("title", ""),
                "location": job.get("location", ""),
                "hard_filter": verdicts[identifier],
                "my_score": "",
                "my_decision": "",
                "notes": "",
                "url": job.get("url", ""),
            }
        )

    reading = sheet.with_suffix(".md")
    write_sheet(sheet, rows)
    write_reading_copy(reading, picked, verdicts)

    kept = sum(1 for row in rows if row["hard_filter"] == "kept")
    print(f"sampled {len(rows)} jobs from {len(jobs)}, {kept} of them survive the hard filter")
    print(f"wrote {sheet} to score in, and {reading} to read from")
    wanted = int(args.size * args.keep_ratio)
    if kept < wanted:
        print(
            f"\nonly {kept_available} jobs in the pool survive the hard filter, so the set is short of the"
            f" {wanted} it aimed for. Add companies to companies.json and rebuild with --force"
            " for a more useful set.",
            file=sys.stderr,
        )
    return 0


def check(args) -> int:
    sheet = pathlib.Path(args.out)
    if not sheet.exists():
        print(f"missing {sheet}, build it first", file=sys.stderr)
        return 1

    with open(sheet, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    problems = []
    scored = 0
    decisions: Counter[str] = Counter()
    for row in rows:
        raw_score = (row.get("my_score") or "").strip()
        decision = (row.get("my_decision") or "").strip().lower()
        if not raw_score and not decision:
            continue
        scored += 1
        try:
            score = int(raw_score)
        except ValueError:
            problems.append(f"{row['id']}: my_score is not a whole number")
            continue
        if not 0 <= score <= 100:
            problems.append(f"{row['id']}: my_score {score} is outside 0 to 100")
        if decision not in {"apply", "review", "skip"}:
            problems.append(f"{row['id']}: my_decision must be apply, review or skip")
        else:
            decisions[decision] += 1

    print(f"{scored} of {len(rows)} rows scored")
    for decision, count in decisions.most_common():
        print(f"  {count:>3}  {decision}")
    if problems:
        print("\nproblems to fix:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    if scored < len(rows):
        print("\nkeep going, the set is only useful once every row is scored")
    else:
        print("\nthe set is complete and ready to measure against")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or check the hand-scored evaluation set.")
    parser.add_argument("--jobs", default="jobs.json", help="input produced by fetch_jobs.py --descriptions --json")
    parser.add_argument("--out", default="evalset.csv", help="the spreadsheet you type scores into")
    parser.add_argument("--size", type=int, default=40, help="how many jobs to sample")
    parser.add_argument("--seed", type=int, default=7, help="fixed so a rebuild returns the same sample")
    parser.add_argument("--keep-ratio", type=float, default=0.6, help="share of the set drawn from jobs the hard filter keeps")
    parser.add_argument("--max-years", type=int, default=filter_jobs.MAX_YEARS_REQUIRED, help="passed to the hard filter for the reference column")
    parser.add_argument("--force", action="store_true", help="overwrite an existing sheet, losing scores already typed")
    parser.add_argument("--check", action="store_true", help="validate the scores typed so far, then stop")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    return check(args) if args.check else build(args)


if __name__ == "__main__":
    raise SystemExit(main())
