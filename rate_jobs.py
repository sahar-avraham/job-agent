"""Score the evaluation set from the terminal, one job at a time.

Typing forty rows into a spreadsheet is slow and easy to abandon halfway. This
shows one job with its description, takes a score, and saves immediately, so the
work survives closing the window and picks up where it stopped.

Nothing here calls the API. This is your judgment being recorded, and it is the
yardstick everything later is measured against.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

COLUMNS = ["id", "company", "title", "location", "hard_filter", "my_score", "my_decision", "notes", "url"]

# Only a suggestion, offered so the common case is one keystroke.
APPLY_AT = 75
REVIEW_AT = 50

RULE = "=" * 78


def load_descriptions(path: pathlib.Path) -> dict[str, str]:
    """Map each job link to its full text, since the sheet carries only the short fields."""
    if not path.exists():
        return {}
    jobs = json.loads(path.read_text(encoding="utf-8"))
    return {job.get("url", ""): job.get("description", "") for job in jobs}


def save(path: pathlib.Path, rows: list[dict]) -> None:
    """Rewrite the whole sheet, which is cheap at this size and keeps every answer safe."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def suggest(score: int) -> str:
    """Turn a score into the decision you most likely mean, leaving you to override it."""
    if score >= APPLY_AT:
        return "apply"
    if score >= REVIEW_AT:
        return "review"
    return "skip"


def show(row: dict, description: str, position: int, total: int, chars: int) -> None:
    """Print one job, trimmed to a readable length."""
    print(f"\n{RULE}")
    print(f"[{position}/{total}]  {row['company']} - {row['title']}")
    print(f"{row['location']}  |  hard filter: {row['hard_filter']}")
    print(f"{row['url']}")
    print(RULE)
    text = description or "(no description was collected for this job)"
    print(text[:chars])
    if len(text) > chars:
        print(f"\n... {len(text) - chars} more characters, press m to see them")


def ask_score(row: dict, description: str, position: int, total: int, chars: int) -> int | str:
    """Read a score, or one of the single-letter commands."""
    shown = chars
    while True:
        answer = input("\nscore 0-100  (m=more text, s=skip, q=quit) > ").strip().lower()
        if answer in {"q", "s"}:
            return answer
        if answer == "m":
            shown += 4000
            print("\n" + (description or "")[:shown])
            continue
        try:
            score = int(answer)
        except ValueError:
            print("  a whole number between 0 and 100, or m, s, q")
            continue
        if 0 <= score <= 100:
            return score
        print("  out of range, 0 to 100")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the evaluation set from the terminal.")
    parser.add_argument("--sheet", default="evalset.csv", help="the sheet to fill in")
    parser.add_argument("--jobs", default="jobs.json", help="where the full job descriptions come from")
    parser.add_argument("--chars", type=int, default=1800, help="how much of the description to show at first")
    parser.add_argument("--redo", action="store_true", help="go through rows that already have a score")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    sheet = pathlib.Path(args.sheet)
    if not sheet.exists():
        print(f"missing {sheet}, run: python make_evalset.py", file=sys.stderr)
        return 1

    with open(sheet, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    descriptions = load_descriptions(pathlib.Path(args.jobs))
    pending = [row for row in rows if args.redo or not (row.get("my_score") or "").strip()]

    if not pending:
        print("every row is already scored, pass --redo to go through them again")
        return 0

    print(f"{len(pending)} rows left of {len(rows)}. Answers save as you go, so quitting is safe.")

    for index, row in enumerate(pending, start=1):
        description = descriptions.get(row["url"], "")
        show(row, description, index, len(pending), args.chars)

        answer = ask_score(row, description, index, len(pending), args.chars)
        if answer == "q":
            break
        if answer == "s":
            continue

        default = suggest(answer)
        decision = input(f"decision [a/r/s]  (Enter = {default}) > ").strip().lower()
        decision = {"a": "apply", "r": "review", "s": "skip"}.get(decision, default)
        note = input("note (Enter to skip) > ").strip()

        row["my_score"] = str(answer)
        row["my_decision"] = decision
        if note:
            row["notes"] = note
        save(sheet, rows)

    done = sum(1 for row in rows if (row.get("my_score") or "").strip())
    print(f"\n{RULE}")
    print(f"saved. {done} of {len(rows)} rows scored.")
    if done == len(rows):
        print("the set is complete and ready to measure against")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
