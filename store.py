"""Remember jobs and scores between runs, in a local SQLite file.

Without this, every run shows the same jobs again and pays to score them again.
With it, a run only asks the model about what it has not already answered.

The scores table is keyed by job and by rubric, so editing profile.md does not
silently reuse an answer that was reached under different rules. Old answers stay
in place, which also makes it possible to compare two rubrics later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
from datetime import date, datetime, timezone

DRAFTS = pathlib.Path("applications")  # where tailor.py writes drafts, relative to the project folder
DB_PATH = "job_agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    location    TEXT,
    url         TEXT NOT NULL,
    description TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    posted      TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    job_id       TEXT NOT NULL,
    rubric       TEXT NOT NULL,
    model        TEXT NOT NULL,
    score        INTEGER,
    fit          INTEGER,
    desirability INTEGER,
    odds         INTEGER,
    decision     TEXT,
    confidence   TEXT,
    reasons      TEXT,
    matched      TEXT,
    gaps         TEXT,
    scored_at    TEXT NOT NULL,
    PRIMARY KEY (job_id, rubric),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    collected   INTEGER DEFAULT 0,
    new_jobs    INTEGER DEFAULT 0,
    kept        INTEGER DEFAULT 0,
    scored      INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_scores_decision ON scores(decision, score);

-- Company, title and link are copied rather than joined, because a posting can be
-- edited or removed after you apply, and some applications never came from a board.
CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT UNIQUE,
    company    TEXT NOT NULL,
    title      TEXT NOT NULL,
    url        TEXT,
    channel    TEXT NOT NULL,
    sent_at    TEXT NOT NULL,
    frozen_dir TEXT
);

-- Append only. The current stage is derived from the latest event, never stored.
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    at             TEXT NOT NULL,
    kind           TEXT NOT NULL,
    source         TEXT NOT NULL,
    note           TEXT,
    evidence       TEXT,
    confirmed      INTEGER NOT NULL DEFAULT 1,
    recorded_at    TEXT NOT NULL,
    FOREIGN KEY (application_id) REFERENCES applications(id)
);

CREATE INDEX IF NOT EXISTS idx_events_application ON events(application_id, at);

-- Jobs you removed from the report. The key is company and title as well as the id,
-- so the same job posted again under a new link stays removed.
CREATE TABLE IF NOT EXISTS dismissed (
    job_id       TEXT PRIMARY KEY,
    key          TEXT NOT NULL,
    company      TEXT NOT NULL,
    title        TEXT NOT NULL,
    dismissed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dismissed_key ON dismissed(key);

-- What each application form was told, question by question, so an interview never
-- meets an answer nobody remembers giving. Keyed by job, because the answers
-- exist from the moment a form is filled, before the application is recorded.
CREATE TABLE IF NOT EXISTS form_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (job_id, question)
);

-- The candidate's answer to a general form question, by its normalised wording, so the next
-- form that asks the same thing is filled without asking again.
CREATE TABLE IF NOT EXISTS answer_memory (
    question    TEXT PRIMARY KEY,
    answer      TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

-- The model's draft for a form question no standing answer covers, kept so reopening the form
-- does not ask the model again. An empty answer means the model had no honest one to give; a
-- flag is the fact check's doubt about the answer, shown to the candidate next to it.
CREATE TABLE IF NOT EXISTS form_drafts (
    job_id      TEXT NOT NULL,
    field_id    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    flag        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (job_id, field_id)
);

-- Small facts about the system itself, such as when the mailbox was last checked.
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Boards that failed to read in the latest collection, kept until one reads again, so a company
-- that moved to another system shows up on the page instead of vanishing quietly.
CREATE TABLE IF NOT EXISTS scout (
    company    TEXT PRIMARY KEY,
    jobs       INTEGER NOT NULL DEFAULT 0,
    careers    TEXT,
    system     TEXT,
    token      TEXT,
    status     TEXT NOT NULL,
    note       TEXT,
    checked_at TEXT NOT NULL,
    seen_at    TEXT
);

CREATE TABLE IF NOT EXISTS board_failures (
    company       TEXT NOT NULL,
    board         TEXT NOT NULL,
    token         TEXT NOT NULL,
    error         TEXT NOT NULL,
    first_failed  TEXT NOT NULL,
    last_failed   TEXT NOT NULL,
    PRIMARY KEY (board, token)
);

-- Every message the mail step has read once, so none is read or classified twice.
CREATE TABLE IF NOT EXISTS mail_messages (
    message_id     TEXT PRIMARY KEY,
    received_at    TEXT NOT NULL,
    sender         TEXT,
    subject        TEXT,
    kind           TEXT,
    company        TEXT,
    title          TEXT,
    confidence     TEXT,
    status         TEXT NOT NULL,
    job_id         TEXT,
    application_id INTEGER,
    detail         TEXT,
    read_at        TEXT NOT NULL
);
"""

# Stages in the order a hiring process runs, then the ways one ends.
STAGES = ["sent", "confirmation", "screening", "assessment", "interview", "offer"]
ENDINGS = ["rejection", "withdrawn", "closed", "silence"]
KINDS = STAGES + ENDINGS + ["note"]
CHANNELS = ["site", "linkedin", "referral", "email", "other"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_id(url: str) -> str:
    """Identify a job by its link, which is the only field every board agrees on."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def rubric_id(profile_text: str) -> str:
    """Fingerprint the rubric, so a changed profile invalidates the answers reached under the old one."""
    return hashlib.sha1(profile_text.encode("utf-8")).hexdigest()[:12]


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    # A database made before the posting date was kept gains the column here.
    if "posted" not in {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}:
        connection.execute("ALTER TABLE jobs ADD COLUMN posted TEXT")
    return connection


def record_board_health(connection: sqlite3.Connection, failed: list[tuple[str, str, str, str]],
                        read: list[tuple[str, str, str]]) -> None:
    """Keep each failing board with the date it first failed, and forget a board once it reads again."""
    at = now()
    for company, board, token, error in failed:
        connection.execute(
            "INSERT INTO board_failures (company, board, token, error, first_failed, last_failed) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (board, token) DO UPDATE SET error = excluded.error, last_failed = excluded.last_failed",
            (company, board, token, error, at, at))
    connection.executemany("DELETE FROM board_failures WHERE board = ? AND token = ?", [(b, t) for _, b, t in read])
    connection.commit()


def board_failures(connection: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in connection.execute("SELECT * FROM board_failures ORDER BY first_failed, company")]


def get_meta(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    connection.commit()


def start_run(connection: sqlite3.Connection) -> int:
    cursor = connection.execute("INSERT INTO runs (started_at) VALUES (?)", (now(),))
    connection.commit()
    return cursor.lastrowid


def finish_run(connection: sqlite3.Connection, run: int, **counts: int) -> None:
    fields = ", ".join(f"{name} = ?" for name in counts)
    connection.execute(
        f"UPDATE runs SET finished_at = ?, {fields} WHERE id = ?",
        (now(), *counts.values(), run),
    )
    connection.commit()


def upsert_jobs(connection: sqlite3.Connection, jobs: list[dict]) -> list[str]:
    """Store every job seen and return the ids of the ones never seen before."""
    stamp = now()
    fresh: list[str] = []
    for job in jobs:
        identifier = job_id(job["url"])
        existing = connection.execute("SELECT 1 FROM jobs WHERE id = ?", (identifier,)).fetchone()
        if existing:
            # Refresh the text too, because a posting can be edited after publication.
            connection.execute(
                # A board that gives no date this time, as Workday does for a description read earlier, keeps the known one.
                "UPDATE jobs SET last_seen = ?, description = ?, title = ?, location = ?,"
                " posted = COALESCE(NULLIF(?, ''), posted) WHERE id = ?",
                (stamp, job.get("description", ""), job.get("title", ""), job.get("location", ""),
                 job.get("posted") or "", identifier),
            )
        else:
            connection.execute(
                "INSERT INTO jobs (id, company, title, location, url, description, first_seen, last_seen, posted)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    job.get("company", ""),
                    job.get("title", ""),
                    job.get("location", ""),
                    job["url"],
                    job.get("description", ""),
                    stamp,
                    stamp,
                    job.get("posted") or None,
                ),
            )
            fresh.append(identifier)
    connection.commit()
    return fresh


def unscored(connection: sqlite3.Connection, ids: list[str], rubric: str) -> list[dict]:
    """Return the jobs among these that carry no answer under the current rubric."""
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT j.* FROM jobs j"
        f" LEFT JOIN scores s ON s.job_id = j.id AND s.rubric = ?"
        f" WHERE j.id IN ({marks}) AND s.job_id IS NULL",
        (rubric, *ids),
    ).fetchall()
    return [dict(row) for row in rows]


def record_score(connection: sqlite3.Connection, identifier: str, rubric: str, model: str, verdict: dict) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO scores"
        " (job_id, rubric, model, score, fit, desirability, odds, decision, confidence,"
        "  reasons, matched, gaps, scored_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            identifier,
            rubric,
            model,
            verdict.get("score"),
            verdict.get("fit"),
            verdict.get("desirability"),
            verdict.get("odds"),
            verdict.get("decision"),
            verdict.get("confidence"),
            json.dumps(verdict.get("reasons", []), ensure_ascii=False),
            json.dumps(verdict.get("matched", []), ensure_ascii=False),
            json.dumps(verdict.get("gaps", []), ensure_ascii=False),
            now(),
        ),
    )
    connection.commit()


def ranked(connection: sqlite3.Connection, rubric: str, minimum: int = 0, ids: list[str] | None = None) -> list[dict]:
    """Return scored jobs, best first, optionally limited to a set of ids."""
    query = (
        "SELECT j.*, s.score, s.fit, s.desirability, s.odds, s.decision, s.confidence,"
        " s.reasons, s.matched, s.gaps"
        " FROM scores s JOIN jobs j ON j.id = s.job_id"
        " WHERE s.rubric = ? AND s.score >= ?"
    )
    params: list = [rubric, minimum]
    if ids:
        query += f" AND j.id IN ({','.join('?' for _ in ids)})"
        params += ids
    query += " ORDER BY s.score DESC"

    out = []
    for row in connection.execute(query, params).fetchall():
        record = dict(row)
        for field in ("reasons", "matched", "gaps"):
            record[field] = json.loads(record[field] or "[]")
        out.append(record)
    return out


def stem(company: str, title: str) -> str:
    """Name the files of one job the same way everywhere they are written or looked up."""
    return re.sub(r"[^a-z0-9]+", "-", f"{company}-{title}".lower()).strip("-")[:60]


def job_stem(job: dict, connection: sqlite3.Connection | None = None) -> str:
    """The file name of one job's drafts and files, told apart from another job with the same company and title.

    The plain name stays with the job whose draft already uses it, so no file written earlier is lost;
    with no draft yet, it goes to the twin seen first. Every other twin adds the start of its own id.
    """
    base = stem(job.get("company", ""), job.get("title", ""))
    own_id = job.get("id") or job_id(job.get("url", ""))
    draft = DRAFTS / f"{base}.json"
    if draft.is_file():
        try:
            owner = job_id(json.loads(draft.read_text(encoding="utf-8"))["job"]["url"])
            return base if owner == own_id else f"{base[:53]}-{own_id[:6]}"
        except (ValueError, KeyError, TypeError):
            pass
    own = connection is None
    connection = connection or sqlite3.connect(DB_PATH)
    try:
        first = connection.execute("SELECT id FROM jobs WHERE company = ? AND title = ? ORDER BY first_seen, id LIMIT 1",
                                   (job.get("company", ""), job.get("title", ""))).fetchone()
    finally:
        if own:
            connection.close()
    return base if first is None or first[0] == own_id else f"{base[:53]}-{own_id[:6]}"


def check_date(text: str) -> str:
    """Accept a calendar date no later than today, because nothing is tracked ahead of time."""
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError("התאריך אינו תקין")
    if day > date.today():
        raise ValueError("התאריך הוא בעתיד")
    return day.isoformat()


def add_application(connection: sqlite3.Connection, company: str, title: str, url: str | None,
                    channel: str, sent_at: str, job: str | None = None, frozen_dir: str | None = None) -> int:
    """Record that something was sent, together with the first event of its log."""
    company, title = (company or "").strip(), (title or "").strip()
    if not company or not title:
        raise ValueError("חסרים שם חברה או תפקיד")
    if channel not in CHANNELS:
        raise ValueError("ערוץ הגשה לא מוכר")
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("הקישור צריך להתחיל ב-http")
    sent_at = check_date(sent_at)

    if job and connection.execute("SELECT 1 FROM applications WHERE job_id = ?", (job,)).fetchone():
        raise LookupError("המשרה הזו כבר מסומנת כהוגשה")
    duplicate = connection.execute(
        "SELECT 1 FROM applications WHERE lower(company) = lower(?) AND lower(title) = lower(?)",
        (company, title),
    ).fetchone()
    if duplicate:
        raise LookupError("כבר קיימת הגשה לאותו תפקיד באותה חברה")

    cursor = connection.execute(
        "INSERT INTO applications (job_id, company, title, url, channel, sent_at, frozen_dir)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job, company, title, url or None, channel, sent_at, frozen_dir),
    )
    add_event(connection, cursor.lastrowid, "sent", sent_at)
    return cursor.lastrowid


def add_event(connection: sqlite3.Connection, application: int, kind: str, at: str, note: str = "",
              source: str = "you", evidence: str | None = None, confirmed: bool = True) -> int:
    """Append one thing that happened to an application."""
    row = connection.execute("SELECT sent_at FROM applications WHERE id = ?", (application,)).fetchone()
    if row is None:
        raise LookupError("ההגשה לא נמצאה")
    if kind not in KINDS:
        raise ValueError("סוג עדכון לא מוכר")
    at = check_date(at)
    if at < row["sent_at"]:
        raise ValueError("התאריך מוקדם מתאריך ההגשה")
    if len(note or "") > 500:
        raise ValueError("ההערה ארוכה מדי")

    cursor = connection.execute(
        "INSERT INTO events (application_id, at, kind, source, note, evidence, confirmed, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (application, at, kind, source, (note or "").strip() or None, evidence, int(confirmed), now()),
    )
    connection.commit()
    return cursor.lastrowid


def remove_application(connection: sqlite3.Connection, application: int) -> None:
    """Delete an application marked by mistake, with its log, which is the only deletion allowed."""
    connection.execute("DELETE FROM events WHERE application_id = ?", (application,))
    if connection.execute("DELETE FROM applications WHERE id = ?", (application,)).rowcount == 0:
        raise LookupError("ההגשה לא נמצאה")
    connection.commit()


def applications(connection: sqlite3.Connection) -> list[dict]:
    """Return every application with its log and the stage that log implies."""
    logs: dict[int, list[dict]] = {}
    for row in connection.execute("SELECT * FROM events ORDER BY at, id"):
        logs.setdefault(row["application_id"], []).append(dict(row))

    out = []
    for row in connection.execute("SELECT * FROM applications ORDER BY sent_at DESC, id DESC"):
        record = dict(row)
        record["events"] = logs.get(record["id"], [])
        moving = [e for e in record["events"] if e["kind"] != "note" and e["confirmed"]]
        record["stage"] = moving[-1]["kind"] if moving else "sent"
        record["last_at"] = moving[-1]["at"] if moving else record["sent_at"]
        record["days_quiet"] = (date.today() - date.fromisoformat(record["last_at"])).days
        out.append(record)
    return out


def applied_jobs(connection: sqlite3.Connection) -> dict[str, dict]:
    """Map job ids to their application, so the report can tell what was already sent.

    An application recorded from mail may carry no job id, so a job also counts as
    applied when its company and title match one.
    """
    by_key = {job_key(row["company"], row["title"]): dict(row) for row in connection.execute("SELECT * FROM applications")}
    out = {row["job_id"]: dict(row) for row in connection.execute("SELECT * FROM applications WHERE job_id IS NOT NULL")}
    for row in connection.execute("SELECT id, company, title FROM jobs"):
        match = by_key.get(job_key(row["company"], row["title"]))
        if match and row["id"] not in out:
            out[row["id"]] = match
    return out


ANSWER_SOURCES = {"standing", "drafted", "you", "file"}


def save_form_answers(connection: sqlite3.Connection, job: str, answers: list[tuple[str, str, str]]) -> None:
    """Store what a form was told, as (question, answer, source), replacing an earlier answer to the same question.

    The source says where the answer came from: answers.md, a per-job draft, typed by hand, or a file attached.
    """
    for question, answer, source in answers:
        if source not in ANSWER_SOURCES:
            raise ValueError(f"unknown answer source: {source}")
        connection.execute(
            "INSERT INTO form_answers (job_id, question, answer, source, recorded_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (job_id, question) DO UPDATE SET answer = excluded.answer, source = excluded.source,"
            " recorded_at = excluded.recorded_at",
            (job, question.strip(), answer.strip(), source, now()),
        )
    connection.commit()


def form_answers(connection: sqlite3.Connection, job: str) -> list[dict]:
    rows = connection.execute("SELECT * FROM form_answers WHERE job_id = ? ORDER BY id", (job,))
    return [dict(row) for row in rows]


def job_key(company: str, title: str) -> str:
    """Reduce company and title to plain words, so a reposted job matches its earlier copy."""
    words = re.findall(r"[a-z0-9֐-׿]+", f"{company} {title}".lower())
    return " ".join(words)


def dismiss_job(connection: sqlite3.Connection, job: str) -> None:
    """Remove a job from the report for good, remembering it by id and by company and title."""
    row = connection.execute("SELECT company, title FROM jobs WHERE id = ?", (job,)).fetchone()
    if row is None:
        raise LookupError("המשרה לא נמצאה במאגר")
    connection.execute(
        "INSERT OR REPLACE INTO dismissed (job_id, key, company, title, dismissed_at) VALUES (?, ?, ?, ?, ?)",
        (job, job_key(row["company"], row["title"]), row["company"], row["title"], now()),
    )
    connection.commit()


def restore_job(connection: sqlite3.Connection, job: str) -> None:
    """Undo a removal, including any repost of the same job removed along with it."""
    posting = connection.execute("SELECT company, title FROM jobs WHERE id = ?", (job,)).fetchone()
    key = job_key(posting["company"], posting["title"]) if posting else None
    if connection.execute("SELECT 1 FROM dismissed WHERE job_id = ? OR key = ?", (job, key)).fetchone() is None:
        raise LookupError("המשרה לא מסומנת כמוסרת")
    connection.execute("DELETE FROM dismissed WHERE job_id = ? OR key = ?", (job, key))
    connection.commit()


def dismissed_jobs(connection: sqlite3.Connection) -> dict[str, dict]:
    """Map every job id that is removed, directly or as a repost of a removed job, to its record."""
    by_key = {row["key"]: dict(row) for row in connection.execute("SELECT * FROM dismissed")}
    out = {}
    for row in connection.execute("SELECT id, company, title FROM jobs"):
        match = by_key.get(job_key(row["company"], row["title"]))
        if match:
            out[row["id"]] = match
    return out


def settled_ids(connection: sqlite3.Connection) -> set[str]:
    """Jobs that need no score and no place in the report, because they were sent or removed."""
    return set(applied_jobs(connection)) | set(dismissed_jobs(connection))


def stats(connection: sqlite3.Connection) -> dict:
    def one(sql: str):
        return connection.execute(sql).fetchone()[0]

    return {
        "jobs": one("SELECT COUNT(*) FROM jobs"),
        "scored": one("SELECT COUNT(*) FROM scores"),
        "rubrics": one("SELECT COUNT(DISTINCT rubric) FROM scores"),
        "runs": one("SELECT COUNT(*) FROM runs"),
        "apply": one("SELECT COUNT(*) FROM scores WHERE decision = 'apply'"),
        "review": one("SELECT COUNT(*) FROM scores WHERE decision = 'review'"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the local job database.")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--profile", default="profile.md")
    parser.add_argument("--top", type=int, default=0, help="also print this many best-scoring jobs")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    connection = connect(args.db)
    for name, value in stats(connection).items():
        print(f"{name:<10}{value:>6}")

    if args.top:
        profile = pathlib.Path(args.profile)
        if not profile.exists():
            print(f"\nmissing {profile}", file=sys.stderr)
            return 1
        rubric = rubric_id(profile.read_text(encoding="utf-8"))
        print(f"\nbest under the current rubric ({rubric})\n")
        for job in ranked(connection, rubric)[: args.top]:
            print(f"{job['score']:>3}  {job['decision']:<7} {job['company'][:14]:<15} {job['title'][:44]}")

    for row in connection.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 5"):
        print(
            f"\nrun {row['id']}  {row['started_at']}  collected {row['collected']},"
            f" new {row['new_jobs']}, kept {row['kept']}, scored {row['scored']}, failed {row['failed']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
