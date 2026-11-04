"""Run the whole pipeline once: collect, filter, score what is new, report.

The point of this file is the word "new". Collecting and filtering are cheap and
happen every time; asking the model is the expensive step, so it only happens for
jobs the database has no answer for under the current rubric. Editing profile.md
changes the rubric fingerprint and correctly makes every answer stale again.
"""

from __future__ import annotations

import use_venv  # noqa: F401, must come first: relaunches under .venv when started with another Python

import argparse
import json
import pathlib
import random
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import catalogue
import fetch_jobs
import filter_jobs
import mail_sync
import report
import scout
import store
import tailor
import techmap
from score_job import MODEL_CLI, score_via_cli


def collect(region: str, descriptions: bool = True, connection=None, read_linkedin: bool = True,
            stats: dict | None = None) -> tuple[list[dict], list[tuple], list[tuple]]:
    """Fetch every configured board and return the jobs in the wanted region, the boards that
    failed with their error, and the boards that read. With a database, the Tech Map's jobs then get
    their full text and LinkedIn's search adds its jobs, once every board is read and can tell which
    of them are duplicates. With stats, what each source gave is noted there."""
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
        unique = scout.complete(unique, connection, read_linkedin, stats)
    if stats is not None:
        stats["boards"] = {"read": sum(1 for entry in read if entry[1] != "techmap"),
                           "failed": sum(1 for entry in failed if entry[1] != "techmap")}
    return unique, failed, read


def source_of(job: dict) -> str:
    return job.get("source") or "boards"


def short_reason(reason: str) -> str:
    """A rule's reason without the word that tripped it, so the same rule counts once."""
    return re.sub(r"^wants \d+\+ years$", "wants more years", re.sub(r" [(].*", "", reason))


def details_of(jobs: list[dict], fresh: set[str], stats: dict, max_years: int, funnel: dict) -> dict:
    """What the run took from each source and what became of its new jobs, kept with the run for the page."""
    sources = {}
    for name in ("boards", "techmap", "linkedin"):
        mine = [j for j in jobs if source_of(j) == name]
        sources[name] = {**stats.get(name, {}), "jobs": len(mine),
                         "new": sum(1 for j in mine if store.job_id(j["url"]) in fresh)}
    new_jobs = [j for j in jobs if store.job_id(j["url"]) in fresh]
    reasons = Counter(short_reason(r) for j in new_jobs if (r := filter_jobs.rejection_reason(j, max_years)))
    return {"sources": sources, "rejected": dict(reasons.most_common()), **funnel}


def print_details(details: dict) -> None:
    """The run's sources and funnel as a few lines, the same counts the page shows."""
    sources, search = details["sources"], details["sources"]["linkedin"]
    print("\nby source:")
    for name, label in (("boards", "company boards"), ("techmap", "Tech Map"), ("linkedin", "LinkedIn search")):
        print(f"  {label:<16} {sources[name]['jobs']:>5} open, {sources[name]['new']:>4} new")
    print(f"  LinkedIn search: {search.get('asked', 0)} searches, {search.get('listed', 0)} listed"
          f" ({search.get('first_listed', 0)} for the first time), {search.get('pages', 0)} job pages read"
          + (f", stopped: {search['stopped']}" if search.get("stopped") else ""))
    print(f"new {details['new']}: {details['new'] - details['passed']} failed the hard rules, {details['passed']} passed;"
          f" of those {details.get('scored_new', 0)} scored, {details['waiting']} wait for their text,"
          f" {details['settled']} sent or removed, {details.get('over_limit', 0)} over the limit,"
          f" {details.get('failed_new', 0)} failed; also scored {details.get('scored_old', 0)} older jobs")


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


def score_jobs(connection, jobs: list[dict], profile: str, rubric: str, model: str, workers: int) -> tuple[set[str], int]:
    """Ask the model about each job and store the answers, returning the ids answered and how many failed."""
    scored_ids: set[str] = set()
    failed = 0

    def one(job: dict):
        try:
            verdict, _ = score_via_cli(profile, job, model)
            return job, verdict.model_dump(), ""
        except Exception as error:
            return job, None, str(error)[:160]

    if jobs:
        print()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for job, verdict, error in pool.map(one, jobs):
                if verdict is None:
                    failed += 1
                    print(f"  !  {job['company']} {job['title'][:40]}: {error}", file=sys.stderr)
                    continue
                store.record_score(connection, store.job_id(job["url"]), rubric, model, verdict)
                scored_ids.add(store.job_id(job["url"]))
                print(f"{verdict['score']:>3}  {verdict['decision']:<7} {job['company'][:14]:<15} {job['title'][:44]}")
    return scored_ids, failed


def write_missing_drafts(connection, shortlist: list[dict], model: str, limit: int) -> None:
    """Write drafts for the jobs above 50 that lack one, so they are ready when the page is opened."""
    out = pathlib.Path("applications")
    drafts = tailor.untailored(connection, shortlist, out)[:limit]
    if drafts:
        print(f"\ntailoring {len(drafts)} drafts for jobs above 50 that have none\n")
        tailor.write_drafts(drafts, tailor.load_facts(pathlib.Path("facts.md")), model, out)


def retry_model(db: str, profile_path: str, model: str = MODEL_CLI, limit: int = 25, workers: int = 4,
                tailor_limit: int = 10) -> None:
    """Ask the model again about the last collection's unanswered jobs, and put the results on the last run."""
    profile = pathlib.Path(profile_path).read_text(encoding="utf-8")
    rubric = store.rubric_id(profile)
    connection = store.connect(db)
    try:
        run = connection.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        latest = connection.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
        seen = [dict(row) for row in connection.execute("SELECT * FROM jobs WHERE last_seen = ?", (latest,))]
        kept = [job for job in seen if filter_jobs.rejection_reason(job, filter_jobs.MAX_YEARS_REQUIRED) is None]
        kept_ids = [job["id"] for job in kept]
        new_kept = {job["id"] for job in kept if job["first_seen"] == latest}
        settled = store.settled_ids(connection)
        todo = [job for job in store.unscored(connection, kept_ids, rubric)
                if job["id"] not in settled and not scout.is_map_job(job)]
        print(f"{len(todo)} need an answer from the model")

        batch = todo[:limit]
        scored_ids, failed = score_jobs(connection, batch, profile, rubric, model, workers)
        shortlist = store.ranked(connection, rubric, minimum=50, ids=kept_ids)

        if run:
            details = json.loads(run["details"]) if run["details"] else None
            if details:
                details.update(
                    scored_new=details.get("scored_new", 0) + len(scored_ids & new_kept),
                    scored_old=details.get("scored_old", 0) + len(scored_ids - new_kept),
                    failed_new=sum(1 for job in batch if job["id"] in new_kept) - len(scored_ids & new_kept),
                    over_limit=sum(1 for job in todo[limit:] if job["id"] in new_kept),
                    above=details.get("above", 0) + sum(1 for job in shortlist if job["id"] in scored_ids))
            store.finish_run(connection, run["id"], scored=run["scored"] + len(scored_ids), failed=failed,
                             details=json.dumps(details) if details else run["details"])
        print(f"\n{len(scored_ids)} scored, {failed} failed")

        write_missing_drafts(connection, shortlist, model, tailor_limit)
        print()
        report.write(db, profile_path, "report.html", 50)
    finally:
        connection.close()


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
            added = catalogue.resweep_if_due(connection)
            if added:
                print("monthly sweep added: " + ", ".join(f"{name} ({board})" for name, board, _ in added))
        except Exception as error:
            print(f"monthly sweep skipped: {error}", file=sys.stderr)
        try:
            techmap.sync()
            before = len(fetch_jobs.COMPANIES)
            scout.check(connection)
            if len(fetch_jobs.COMPANIES) > before:
                print("scout added: " + ", ".join(f"{name} ({board})" for name, board, _ in fetch_jobs.COMPANIES[before:]))
        except Exception as error:
            print(f"scout skipped: {error}", file=sys.stderr)

    stats: dict = {}
    jobs, failed, read = collect(args.region, connection=connection, read_linkedin=not args.no_linkedin, stats=stats)
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

    fresh_ids = set(fresh)
    # Where each new job that passed the rules went, so the page can say why fewer were scored than passed.
    new_kept = {store.job_id(j["url"]) for j in kept} & fresh_ids
    funnel = {"new": len(fresh), "passed": len(new_kept),
              "settled": sum(1 for j in skipped if store.job_id(j["url"]) in new_kept),
              "waiting": sum(1 for j in blind if store.job_id(j["url"]) in new_kept)}

    if args.dry_run:
        for job in todo[:20]:
            print(f"  would score  {job['company'][:14]:<15} {job['title'][:48]}")
        store.finish_run(connection, run, collected=len(jobs), new_jobs=len(fresh), kept=len(kept), scored=0, failed=0,
                         details=json.dumps(details_of(jobs, fresh_ids, stats, args.max_years, funnel)))
        return 0

    if args.mix:
        todo = mixed(todo, args.seed)
    funnel["over_limit"] = sum(1 for j in todo[args.limit:] if store.job_id(j["url"]) in new_kept)
    todo = todo[: args.limit]
    scored_ids, failed = score_jobs(connection, todo, profile, rubric, args.model, args.workers)
    scored = len(scored_ids)

    shortlist = store.ranked(connection, rubric, minimum=50, ids=kept_ids)
    fresh_pass = sum(1 for job in todo if any(s["id"] == store.job_id(job["url"]) for s in shortlist))
    funnel.update(scored_new=len(scored_ids & new_kept), scored_old=len(scored_ids - new_kept),
                  failed_new=sum(1 for j in todo if store.job_id(j["url"]) in new_kept) - len(scored_ids & new_kept),
                  above=fresh_pass)
    details = details_of(jobs, fresh_ids, stats, args.max_years, funnel)
    store.finish_run(connection, run, collected=len(jobs), new_jobs=len(fresh), kept=len(kept), scored=scored,
                     failed=failed, details=json.dumps(details))
    print_details(details)
    # One line with the whole funnel, because the numbers in between are what say where a run lost jobs.
    print(f"\nrun {run}: collected {len(jobs)}, new {len(fresh)}, passed the hard rules {len(kept)},"
          f" sent to the model {scored}, of those above 50 {fresh_pass}, failed {failed}")

    print(f"\nworth your attention, {len(shortlist)} of {len(kept)} currently open:\n")
    for job in shortlist:
        print(f"{job['score']:>3}  {job['decision']:<7} {job['company'][:14]:<15} {job['title'][:40]}")
        print(f"     {job['url']}")
    if not shortlist:
        print("  nothing above 50 this time")

    # Approval stays a click on the page, since it is where the drafts are read before anything is sent.
    if not args.no_tailor:
        write_missing_drafts(connection, shortlist, args.model, args.tailor_limit)

    if not args.no_report:
        print()
        report.write(args.db, args.profile, "report.html", 50, args.open)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
