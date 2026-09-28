"""Ask one question about each job the model has judged but you have not.

This is the cheap check, not a second evaluation set. It answers whether the
ranking is good enough to build the rest of the project on, in about five
minutes, by asking the only question the report will ever ask you.

Two rules make the answers worth having. The model's verdict is never shown
before you answer, because seeing it would pull your answer towards it. And the
order is shuffled rather than ranked, so position cannot leak the ranking either.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import random
import re
import sys

import filter_jobs
import store

COLUMNS = ["id", "company", "title", "would_apply", "my_score", "notes", "url"]
ANSWERS = {"y": "yes", "n": "no", "m": "maybe"}


def normalised(title: str) -> str:
    """Reduce a title to its core words, so two spellings of one role collapse together."""
    return re.sub(r"[^a-z0-9 ]+", " ", title.lower()).split("(")[0].strip()


def candidates(connection, rubric: str, already_rated: set[str], run_seen: set[str]) -> list[dict]:
    """Return jobs to ask about: scored under this rubric, still open, and never rated by hand."""
    out, seen_titles = [], set()
    for job in store.ranked(connection, rubric):
        if job["id"] in already_rated or job["id"] not in run_seen:
            continue
        key = (job["company"].lower(), normalised(job["title"]))
        if key in seen_titles:
            continue
        seen_titles.add(key)
        out.append(job)
    return out


def sample(jobs: list[dict], threshold: int, top: int, below: int) -> list[dict]:
    """Pick the few jobs worth asking about, rather than every job that was scored.

    Only two answers change anything: whether the jobs the model surfaced deserve
    to be there, and whether it buried something. So take the highest scores, which
    are what you would actually see, and a spread from underneath them, which is
    where a buried job would be hiding.
    """
    above = [j for j in jobs if j["score"] >= threshold][:top]
    rest = [j for j in jobs if j["score"] < threshold]
    # An even spread rather than the top of the rejects, so the whole range is tested.
    step = max(len(rest) // below, 1) if below else 1
    return above + rest[::step][:below]


def load_rated(path: pathlib.Path) -> set[str]:
    """Read the ids already carrying a hand score, so nothing is asked twice."""
    if not path.exists():
        return set()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return {row["id"] for row in csv.DictReader(handle) if (row.get("my_score") or "").strip()}


def load_answers(path: pathlib.Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def save_answers(path: pathlib.Path, answers: dict[str, dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(answers.values())


def ask(jobs: list[dict], path: pathlib.Path, answers: dict[str, dict], chars: int) -> None:
    todo = [job for job in jobs if job["id"] not in answers]
    if not todo:
        print("every job in this set is already answered, pass --report to see the result")
        return

    random.Random(11).shuffle(todo)
    print(f"{len(todo)} jobs to look at. The model's verdict stays hidden until you finish.\n")

    for position, job in enumerate(todo, start=1):
        years = filter_jobs.required_years(job.get("description") or "")
        print("=" * 78)
        print(f"[{position}/{len(todo)}]  {job['company']} - {job['title']}")
        print(f"{job['location']}  |  demands {years} years of experience" if years else f"{job['location']}  |  no experience figure stated")
        print(job["url"])
        print("=" * 78)
        text = job.get("description") or "(no description was collected)"
        print(text[:chars])
        if len(text) > chars:
            print(f"\n... {len(text) - chars} more characters, press + to see them")

        while True:
            reply = input("\nwould you apply?  y / n / m for maybe  (+=more text, s=skip, q=quit) > ").strip().lower()
            if reply == "+" and len(text) > chars:   # a separate key from maybe, which shared m
                print("\n" + text[:chars * 3])
                continue
            if reply in ANSWERS or reply in {"s", "q"}:
                break
            print("  answer y, n or m, or + for more text, s to skip, q to stop")

        if reply == "q":
            break
        if reply == "s":
            continue

        # Optional, because the decision is what the report acts on. A number adds
        # ordering inside an answer, which the three choices alone cannot show.
        score = ""
        while True:
            raw = input("score 0-100, optional (Enter to skip) > ").strip()
            if not raw:
                break
            if raw.isdigit() and 0 <= int(raw) <= 100:
                score = raw
                break
            print("  a whole number between 0 and 100, or Enter to skip")

        note = input("note (Enter to skip) > ").strip()
        answers[job["id"]] = {
            "id": job["id"],
            "company": job["company"],
            "title": job["title"],
            "would_apply": ANSWERS[reply],
            "my_score": score,
            "notes": note,
            "url": job["url"],
        }
        save_answers(path, answers)

    print(f"\nsaved. {len(answers)} of {len(jobs)} answered.")


def report(jobs: list[dict], answers: dict[str, dict], threshold: int) -> None:
    """Check the two failures that matter: junk near the top, and good jobs buried."""
    paired = [(job, answers[job["id"]]) for job in jobs if job["id"] in answers]
    if not paired:
        print("nothing answered yet", file=sys.stderr)
        return

    above = [(j, a) for j, a in paired if j["score"] >= threshold]
    below = [(j, a) for j, a in paired if j["score"] < threshold]

    wrong_at_top = [(j, a) for j, a in above if a["would_apply"] == "no"]
    buried = [(j, a) for j, a in below if a["would_apply"] == "yes"]

    print(f"\n{len(paired)} jobs answered, threshold {threshold}\n")
    print(f"surfaced to you   {len(above):>3}   of which you would not apply to {len(wrong_at_top)}")
    print(f"hidden from you   {len(below):>3}   of which you would apply to     {len(buried)}")

    if wrong_at_top:
        print("\nnoise near the top, the model surfaced these and you would not apply:")
        for job, answer in sorted(wrong_at_top, key=lambda p: -p[0]["score"]):
            print(f"  {job['score']:>3}  {job['company'][:14]:<15} {job['title'][:40]}  {answer['notes']}")

    if buried:
        print("\nburied, you would apply and the model hid them. This is the costlier failure:")
        for job, answer in sorted(buried, key=lambda p: -p[0]["score"]):
            print(f"  {job['score']:>3}  {job['company'][:14]:<15} {job['title'][:40]}  {answer['notes']}")

    if not wrong_at_top and not buried:
        print("\nneither failure appeared. The ranking is good enough to build on.")

    print("\nyour answer against the model's score:")
    for label in ("yes", "maybe", "no"):
        scores = [j["score"] for j, a in paired if a["would_apply"] == label]
        if scores:
            print(f"  {label:<6} n={len(scores):<3} model scores {min(scores)} to {max(scores)}, average {sum(scores)/len(scores):.0f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="A five minute check on the ranking.")
    parser.add_argument("--db", default=store.DB_PATH)
    parser.add_argument("--profile", default="profile.md")
    parser.add_argument("--evalset", default="evalset.csv", help="hand scores already given, never asked about again")
    parser.add_argument("--out", default="shortlist_ratings.csv")
    parser.add_argument("--threshold", type=int, default=50, help="the score at which a job reaches you")
    parser.add_argument("--chars", type=int, default=1600)
    parser.add_argument("--top", type=int, default=12, help="how many of the highest scoring jobs to ask about")
    parser.add_argument("--below", type=int, default=8, help="how many from under the threshold, spread across the range")
    parser.add_argument("--report", action="store_true", help="show the result instead of asking")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    profile = pathlib.Path(args.profile)
    if not profile.exists():
        print(f"missing {profile}", file=sys.stderr)
        return 1

    connection = store.connect(args.db)
    rubric = store.rubric_id(profile.read_text(encoding="utf-8"))

    latest = connection.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
    run_seen = {row[0] for row in connection.execute("SELECT id FROM jobs WHERE last_seen = ?", (latest,))}

    already = load_rated(pathlib.Path(args.evalset))
    jobs = sample(candidates(connection, rubric, already, run_seen), args.threshold, args.top, args.below)
    if not jobs:
        print("no unrated jobs under the current rubric, run run.py first", file=sys.stderr)
        return 1

    path = pathlib.Path(args.out)
    answers = load_answers(path)

    if args.report:
        report(jobs, answers, args.threshold)
    else:
        above = sum(1 for j in jobs if j["score"] >= args.threshold)
        print(f"{len(jobs)} jobs to check: {above} the model would show you, {len(jobs) - above} it would hide.")
        ask(jobs, path, answers, args.chars)
        report(jobs, answers, args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
