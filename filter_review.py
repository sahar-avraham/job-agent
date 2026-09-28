"""Break down model scores by what the hard filter can see, to find rules worth changing.

The filter runs before any model call and sees only the title and the description
text. The model sees everything and gives a score. Where a group the filter can
recognise scores almost entirely below the threshold, a drop rule would have saved
those calls; where it scores well, similar jobs the filter drops deserve a look.

No model call happens here. It only reads scores already in the database.

    python filter_review.py            the latest run
    python filter_review.py --all      every job scored under the current rubric
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import defaultdict

import filter_jobs
import store

# First match wins, so the more specific kinds come first.
ROLE_KINDS = [
    ("security analyst", r"analyst|threat hunt|dfir"),
    ("security engineer", r"security|devsecops|appsec|cyber"),
    ("devops / sre / platform", r"devops|\bsre\b|reliability|platform|infrastructure|cloud"),
    ("data / ml / ai", r"\bdata\b|machine learning|\bml\b|\bai\b|llm|algorithm|computer vision|research"),
    ("qa / automation", r"\bqa\b|quality|automation|test"),
    ("support", r"support|help ?desk|tier \d"),
    ("embedded / low level", r"embedded|firmware|\bc\b|c\+\+|kernel|linux|driver|dsp|rtos"),
    ("mobile", r"mobile|android|ios"),
    ("frontend", r"front ?end|\bui\b|react|angular"),
    ("full stack", r"full ?stack"),
    ("backend", r"back ?end|server|java|python|\bgo\b|node"),
    ("general software", r"software|developer|programmer"),
    ("hebrew title", r"[֐-׿]"),
    ("other engineering", r"."),
]


def role_kind(title: str) -> str:
    for name, pattern in ROLE_KINDS:
        if re.search(pattern, title, re.I):
            return name
    return "other engineering"


def years_bucket(description: str) -> str:
    years = filter_jobs.required_years(description)
    return "no figure" if years is None else f"{years} years"


def features(job: dict) -> dict[str, str]:
    title, description = job["title"], job.get("description") or ""
    return {
        "role kind": role_kind(title),
        "years floor": years_bucket(description),
        "junior title": "yes" if filter_jobs.JUNIOR_TITLE.search(title) else "no",
        "hebrew posting": "yes" if re.search(r"[֐-׿]{3,}", description) else "no",
        "company": job["company"],
    }


def table(title: str, groups: dict[str, list[dict]], threshold: int, minimum: int) -> None:
    print(f"\n{title}")
    print(f"  {'group':<26}{'jobs':>5}{'mean':>6}{'>=' + str(threshold):>6}{'apply':>6}")
    for name, jobs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(jobs) < minimum:
            continue
        scores = [j["score"] for j in jobs]
        above = sum(s >= threshold for s in scores)
        apply = sum(j["decision"] == "apply" for j in jobs)
        flag = "   <- none above threshold" if above == 0 and len(jobs) >= 4 else ""
        print(f"  {name[:25]:<26}{len(jobs):>5}{sum(scores) / len(scores):>6.0f}{above:>6}{apply:>6}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Break scores down by what the hard filter can see.")
    parser.add_argument("--db", default=store.DB_PATH)
    parser.add_argument("--profile", default="profile.md")
    parser.add_argument("--all", action="store_true", help="every job scored under the current rubric")
    parser.add_argument("--threshold", type=int, default=50)
    parser.add_argument("--min-group", type=int, default=2, help="hide groups smaller than this")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    connection = store.connect(args.db)
    rubric = store.rubric_id(pathlib.Path(args.profile).read_text(encoding="utf-8"))
    query = ("SELECT j.*, s.score, s.decision, s.gaps, s.scored_at FROM scores s JOIN jobs j ON j.id = s.job_id"
             " WHERE s.rubric = ?")
    params: list = [rubric]
    if not args.all:
        run = connection.execute("SELECT started_at FROM runs WHERE scored > 0 ORDER BY id DESC LIMIT 1").fetchone()
        if run is None:
            print("no run has scored anything yet")
            return 1
        query += " AND s.scored_at >= ?"
        params.append(run["started_at"])
    jobs = [dict(r) for r in connection.execute(query, params)]
    if not jobs:
        print("nothing scored in that range")
        return 1

    above = sum(j["score"] >= args.threshold for j in jobs)
    print(f"{len(jobs)} jobs, {above} at or above {args.threshold}, "
          f"{sum(j['decision'] == 'apply' for j in jobs)} marked apply")

    by: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for job in jobs:
        for name, value in features(job).items():
            by[name][value].append(job)
    for name in ("role kind", "years floor", "junior title", "hebrew posting"):
        table(name, by[name], args.threshold, 1)
    table("company, groups of at least two", by["company"], args.threshold, max(args.min_group, 2))

    print("\nlowest scores, with the model's first stated gap:")
    for job in sorted(jobs, key=lambda j: j["score"])[:15]:
        gap = (store.json.loads(job["gaps"] or "[]") or [""])[0]
        print(f"  {job['score']:>3}  {job['company'][:14]:<15}{job['title'][:40]:<41}{gap[:70]}")

    print("\nhighest scores:")
    for job in sorted(jobs, key=lambda j: -j["score"])[:10]:
        print(f"  {job['score']:>3}  {job['decision']:<7}{job['company'][:14]:<15}{job['title'][:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
