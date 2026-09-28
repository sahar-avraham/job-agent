"""Measure the model's scoring against the scores you assigned by hand.

This is the only place the project finds out whether the rubric works. Without it,
every change to the prompt or the weights is a guess dressed up as an improvement.

Run with --score first, which asks the model about each job in the evaluation set
and caches the answers. Then run it again with no arguments to see the report. The
cache means tuning the rubric costs one model run, not one per look at the numbers.
"""

from __future__ import annotations

import use_venv  # noqa: F401, must come first: relaunches under .venv when started with another Python

import argparse
import csv
import json
import pathlib
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import claude_cli
from score_job import MODEL_CLI, Verdict, score_via_cli

DECISIONS = ["apply", "review", "skip"]


def load_rows(sheet: pathlib.Path) -> list[dict]:
    with open(sheet, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_cache(path: pathlib.Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def rank(values: list[float]) -> list[float]:
    """Rank values, sharing a rank between ties, which is what a rank correlation needs."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        stop = index
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
            stop += 1
        shared = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[order[position]] = shared
        index = stop + 1
    return ranks


def correlation(left: list[float], right: list[float]) -> float:
    """Pearson correlation, which on ranked inputs gives Spearman."""
    n = len(left)
    if n < 2:
        return 0.0
    mean_l, mean_r = sum(left) / n, sum(right) / n
    cov = sum((a - mean_l) * (b - mean_r) for a, b in zip(left, right))
    var_l = sum((a - mean_l) ** 2 for a in left) ** 0.5
    var_r = sum((b - mean_r) ** 2 for b in right) ** 0.5
    return cov / (var_l * var_r) if var_l and var_r else 0.0


def score_all(rows: list[dict], jobs: dict[str, dict], profile: str, cache_path: pathlib.Path, workers: int, model: str) -> None:
    """Ask the model about every row missing from the cache, saving as each answer lands."""
    cache = load_cache(cache_path)
    todo = [row for row in rows if row["id"] not in cache and row["url"] in jobs]
    if not todo:
        print("every row is already in the cache, nothing to ask")
        return

    print(f"asking the model about {len(todo)} jobs, {workers} at a time")

    def one(row: dict) -> tuple[str, dict | None, str]:
        try:
            verdict, _ = score_via_cli(profile, jobs[row["url"]], model)
            return row["id"], verdict.model_dump(), ""
        except Exception as error:
            return row["id"], None, str(error)[:160]

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for identifier, verdict, error in pool.map(one, todo):
            done += 1
            if verdict is None:
                print(f"  [{done}/{len(todo)}] {identifier} failed: {error}", file=sys.stderr)
                continue
            cache[identifier] = verdict
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [{done}/{len(todo)}] {identifier} scored {verdict['score']}")


def report(rows: list[dict], cache: dict[str, dict]) -> None:
    """Print how far the model is from you, on decisions and on ordering."""
    paired = [
        (row, cache[row["id"]])
        for row in rows
        if row["id"] in cache and (row.get("my_score") or "").strip()
    ]
    if not paired:
        print("nothing to compare, run with --score first", file=sys.stderr)
        return

    mine = [int(row["my_score"]) for row, _ in paired]
    theirs = [verdict["score"] for _, verdict in paired]

    agree = sum(1 for row, verdict in paired if row["my_decision"].strip().lower() == verdict["decision"])
    errors = [abs(a - b) for a, b in zip(mine, theirs)]

    print(f"\ncompared {len(paired)} jobs\n")
    print(f"decision agreement   {agree}/{len(paired)}  ({agree / len(paired):.0%})")
    print(f"rank correlation     {correlation(rank(mine), rank(theirs)):+.2f}   (1.0 means identical ordering)")
    print(f"average gap          {sum(errors) / len(errors):.1f} points")
    print(f"largest gap          {max(errors)} points")

    print("\nwhere the decisions landed, your rows against the model's columns")
    header = "            " + "".join(f"{d:>9}" for d in DECISIONS)
    print(header)
    for yours in DECISIONS:
        line = f"{yours:>10}  "
        for theirs_decision in DECISIONS:
            count = sum(
                1
                for row, verdict in paired
                if row["my_decision"].strip().lower() == yours and verdict["decision"] == theirs_decision
            )
            line += f"{count:>9}"
        print(line)

    print("\nthe ten biggest disagreements")
    print(f"{'yours':>6}{'model':>7}{'gap':>6}  {'company':<14} title")
    worst = sorted(paired, key=lambda pair: -abs(int(pair[0]["my_score"]) - pair[1]["score"]))[:10]
    for row, verdict in worst:
        gap = int(row["my_score"]) - verdict["score"]
        arrow = "model too low" if gap > 0 else "model too high"
        print(f"{row['my_score']:>6}{verdict['score']:>7}{gap:>+6}  {row['company'][:14]:<14} {row['title'][:34]}  {arrow}")

    print("\nwhat the model says about the jobs you would apply to")
    for row, verdict in paired:
        if row["my_decision"].strip().lower() == "apply":
            print(f"  you {row['my_score']}, model {verdict['score']} ({verdict['decision']}) - {row['company']} {row['title'][:40]}")
            for reason in verdict["reasons"][:2]:
                print(f"      {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the model's scoring against your own.")
    parser.add_argument("--sheet", default="evalset.csv")
    parser.add_argument("--jobs", default="jobs.json")
    parser.add_argument("--profile", default="profile.md")
    parser.add_argument("--cache", default="evalset_model.json", help="model answers, kept so tuning does not re-ask")
    parser.add_argument("--score", action="store_true", help="ask the model about rows missing from the cache")
    parser.add_argument("--workers", type=int, default=3, help="how many questions to run at once")
    parser.add_argument("--model", default=MODEL_CLI)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    sheet = pathlib.Path(args.sheet)
    if not sheet.exists():
        print(f"missing {sheet}, run: python make_evalset.py", file=sys.stderr)
        return 1

    rows = load_rows(sheet)
    jobs = {job["url"]: job for job in json.loads(pathlib.Path(args.jobs).read_text(encoding="utf-8"))}
    cache_path = pathlib.Path(args.cache)

    if args.score:
        profile = pathlib.Path(args.profile).read_text(encoding="utf-8")
        score_all(rows, jobs, profile, cache_path, args.workers, args.model)

    report(rows, load_cache(cache_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
