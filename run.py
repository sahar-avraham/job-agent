"""Run the whole pipeline once: collect, filter, score what is new, report.

The point of this file is the word "new". Collecting and filtering are cheap and
happen every time; asking the model is the expensive step, so it only happens for
jobs the database has no answer for under the current rubric. Editing profile.md
changes the rubric fingerprint and correctly makes every answer stale again.
"""

from __future__ import annotations

import use_venv  # noqa: F401, must come first: relaunches under .venv when started with another Python

import argparse
import pathlib
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import fetch_jobs
import filter_jobs
import mail_sync
import report
import scout
import store
import tailor
import techmap
from score_job import MODEL_CLI, score_via_cli


def collect(region: str, descriptions: bool = True, connection=None,
            read_linkedin: bool = True) -> tuple[list[dict], list[tuple], list[tuple]]:
    """Fetch every configured board and return the jobs in the wanted region, the boards that
    failed with their error, and the boards that read. With a database, the Tech Map's jobs then get
    their full text, once every board is read and can tell which of them are duplicates."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda entry: fetch_jobs.fetch_company(entry, descriptions), fetch_jobs.COMPANIES))

    jobs, failed, read = [], [], []
    for entry, (company, company_jobs, error) in zip(fetch_jobs.COMPANIES, results):
        jobs.extend(company_jobs)
        if error:
            failed.append((*entry, error))
        else:
            read.append(entry)
    if failed:
        print("could not read: " + ", ".join(f"{f[0]} ({f[3]})" for f in failed), file=sys.stderr)

    places = fetch_jobs.REGIONS.get(region, [region] if region else [])
    kept = [job for job in jobs if fetch_jobs.matches(job, [], places)]

    seen, unique = set(), []
    for job in kept:
        key = (job.company.lower(), job.title.strip().lower(), job.location.strip().lower())
        if key not in seen:
            seen.add(key)
            unique.append(asdict(job))
    if connection is not None and descriptions:
        unique = scout.complete(unique, connection, read_linkedin)
    return unique, failed, read


def mixed(jobs: list[dict], seed: int) -> list[dict]:
    """Deal jobs one company at a time, so a partial batch is not all from the first companies collected."""
    rng = random.Random(seed)
    by_company: dict[str, list[dict]] = {}
    for job in jobs:
        by_company.setdefault(job["company"], []).append(job)
    queues = list(by_company.values())
    for queue in queues:
        rng.shuffle(queue)
    rng.shuffle(queues)
    out = []
    while queues:
        out.extend(queue.pop() for queue in queues)
        queues = [queue for queue in queues if queue]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect, filter and score in one pass.")
    parser.add_argument("--region", default="israel", help="a region name from fetch_jobs.REGIONS")
    parser.add_argument("--profile", default="profile.md")
    parser.add_argument("--db", default=store.DB_PATH)
    parser.add_argument("--max-years", type=int, default=filter_jobs.MAX_YEARS_REQUIRED)
    parser.add_argument("--limit", type=int, default=25, help="most jobs to score in one run, so a surprise cannot run away")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default=MODEL_CLI)
    parser.add_argument("--dry-run", action="store_true", help="collect and filter only, ask the model nothing")
    parser.add_argument("--rescore", action="store_true", help="also score jobs already answered under this rubric")
    parser.add_argument("--no-report", action="store_true", help="skip writing report.html at the end")
    parser.add_argument("--open", action="store_true", help="open the report in a browser when it is written")
    parser.add_argument("--no-mail", action="store_true", help="skip reading the mailbox for applications and replies")
    parser.add_argument("--mix", action="store_true", help="take the batch across companies instead of in collection order")
    parser.add_argument("--seed", type=int, default=0, help="makes --mix pick the same batch again")
    parser.add_argument("--tailor-limit", type=int, default=10, help="most new drafts to tailor in one run")
    parser.add_argument("--no-tailor", action="store_true", help="skip tailoring drafts for jobs above 50")
    parser.add_argument("--no-scout", action="store_true", help="skip looking for hiring companies no board reads")
    parser.add_argument("--no-linkedin", action="store_true", help="look for the Tech Map's job texts on DevJobs only")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    profile_path = pathlib.Path(args.profile)
    if not profile_path.exists():
        print(f"missing {profile_path}", file=sys.stderr)
        return 1
    profile = profile_path.read_text(encoding="utf-8")
    rubric = store.rubric_id(profile)

    connection = store.connect(args.db)
    run = store.start_run(connection)
    print(f"run {run}, rubric {rubric}\n")

    # The scout first, so a company it adds is collected in this same run.
    if not args.no_scout:
        try:
            techmap.sync()
            before = len(fetch_jobs.COMPANIES)
            scout.check(connection)
            if len(fetch_jobs.COMPANIES) > before:
                print("scout added: " + ", ".join(f"{name} ({board})" for name, board, _ in fetch_jobs.COMPANIES[before:]))
        except Exception as error:
            print(f"scout skipped: {error}", file=sys.stderr)

    jobs, failed, read = collect(args.region, connection=connection, read_linkedin=not args.no_linkedin)
    store.record_board_health(connection, failed, read)
    fresh = store.upsert_jobs(connection, jobs)
    print(f"collected {len(jobs)} positions in {args.region}, {len(fresh)} of them new")

    kept = [job for job in jobs if filter_jobs.rejection_reason(job, args.max_years) is None]
    print(f"{len(kept)} passed the hard rules")

    if not args.no_mail and not args.dry_run:
        mail_sync.sync(connection, args.model)

    # A job already sent or removed by hand is never scored, so its model call is not spent.
    settled = store.settled_ids(connection)
    kept_ids = [store.job_id(job["url"]) for job in kept]
    todo = kept if args.rescore else store.unscored(connection, kept_ids, rubric)
    skipped = [job for job in todo if store.job_id(job["url"]) in settled]
    todo = [job for job in todo if store.job_id(job["url"]) not in settled]
    # A job known only by its title is never scored: without the posting's requirements a score is a guess.
    # It waits on the page until its full text is found.
    blind = [job for job in todo if scout.is_map_job(job)]
    todo = [job for job in todo if not scout.is_map_job(job)]
    print(f"{len(todo)} need an answer from the model" + (f", {len(skipped)} skipped as sent or removed" if skipped else "")
          + (f", {len(blind)} wait for their full text" if blind else ""))

    if args.dry_run:
        for job in todo[:20]:
            print(f"  would score  {job['company'][:14]:<15} {job['title'][:48]}")
        store.finish_run(connection, run, collected=len(jobs), new_jobs=len(fresh), kept=len(kept), scored=0, failed=0)
        return 0

    if args.mix:
        todo = mixed(todo, args.seed)
    todo = todo[: args.limit]
    scored = failed = 0

    def one(job: dict):
        try:
            verdict, _ = score_via_cli(profile, job, args.model)
            return job, verdict.model_dump(), ""
        except Exception as error:
            return job, None, str(error)[:160]

    if todo:
        print()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for job, verdict, error in pool.map(one, todo):
                if verdict is None:
                    failed += 1
                    print(f"  !  {job['company']} {job['title'][:40]}: {error}", file=sys.stderr)
                    continue
                store.record_score(connection, store.job_id(job["url"]), rubric, args.model, verdict)
                scored += 1
                print(f"{verdict['score']:>3}  {verdict['decision']:<7} {job['company'][:14]:<15} {job['title'][:44]}")

    store.finish_run(connection, run, collected=len(jobs), new_jobs=len(fresh), kept=len(kept), scored=scored, failed=failed)

    shortlist = store.ranked(connection, rubric, minimum=50, ids=kept_ids)
    fresh_pass = sum(1 for job in todo if any(s["id"] == store.job_id(job["url"]) for s in shortlist))
    # One line with the whole funnel, because the numbers in between are what say where a run lost jobs.
    print(f"\nrun {run}: collected {len(jobs)}, new {len(fresh)}, passed the hard rules {len(kept)},"
          f" sent to the model {scored}, of those above 50 {fresh_pass}, failed {failed}")

    print(f"\nworth your attention, {len(shortlist)} of {len(kept)} currently open:\n")
    for job in shortlist:
        print(f"{job['score']:>3}  {job['decision']:<7} {job['company'][:14]:<15} {job['title'][:40]}")
        print(f"     {job['url']}")
    if not shortlist:
        print("  nothing above 50 this time")

    # Drafts are written for the jobs above 50 that lack one, so they are ready when the page is opened.
    # Approval stays a click on the page, since it is where the drafts are read before anything is sent.
    if not args.no_tailor:
        out = pathlib.Path("applications")
        drafts = tailor.untailored(connection, shortlist, out)[: args.tailor_limit]
        if drafts:
            print(f"\ntailoring {len(drafts)} drafts for jobs above 50 that have none\n")
            tailor.write_drafts(drafts, tailor.load_facts(pathlib.Path("facts.md")), args.model, out)

    if not args.no_report:
        print()
        report.write(args.db, args.profile, "report.html", 50, args.open)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
