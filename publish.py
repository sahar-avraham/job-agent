"""Build the public copy of this project in a separate folder, and refuse if anything personal is in it.

This folder holds the candidate's facts, CVs, applications, mailbox password and
database next to the code, so it is never pushed as it is. Instead this copies an
explicit list of files into a fresh folder. A file that is not on the list does not
exist there, whatever .gitignore says.

Then it scans the copy for anything that must not be public: every value in .env,
every line of facts.md, profile.md and answers.md, and the phone and email patterns.
One hit and it stops, deletes nothing, and says where. The copy is only ever built,
never pushed; pushing is left to the person, after reading what is in it.

    python publish.py                   build ../job-agent-public and check it
    python publish.py --out some/path   build somewhere else
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent

# Everything public, and nothing else. Add a file here on purpose, never by pattern.
PUBLIC = [
    "README.md", "COMMANDS.md", "requirements.txt", ".gitignore",
    ".env.example", "facts.example.md", "profile.example.md", "answers.example.md", "companies.example.json",
    "approve.py", "calibrate.py", "catalogue.py", "claude_cli.py", "comeet.py", "cv_layout.py",
    "discover_boards.py", "fetch_jobs.py", "filter_jobs.py", "filter_review.py", "keywords.py",
    "mail_sync.py", "make_evalset.py", "publish.py", "rate_jobs.py", "rate_shortlist.py",
    "render_docx.py", "report.py", "run.py", "score_job.py", "serve.py", "settings.py", "store.py",
    "submit.py", "tailor.py", "tracking.py", "use_venv.py", "browser.js", "tracking.js",
]

# Short values would match everywhere, so only lines and values at least this long are searched for.
MIN_LENGTH = 12
# Fact lines shorter than this are skill names and headlines anyone might write, like "GitHub Actions".
FACT_LENGTH = 40
PHONE = re.compile(r"\b0\d{1,2}-?\d{7}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# Addresses the code legitimately contains: examples, services, and the placeholders above.
ALLOWED_EMAILS = re.compile(r"example\.com$|^you@gmail\.com$|noreply@anthropic\.com$|applynow\.io$|linkedin\.com$", re.I)


def secrets() -> list[str]:
    """Every private string the copy must not contain, gathered from the private files themselves."""
    found: list[str] = []
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                # The app password is also written with its spaces removed.
                found += [value, value.replace(" ", "")]
    # Only the personal parts of each file: fact lines, the answers themselves, and the profile's
    # facts and preferences. Headings, instructions and the scoring method are not private.
    facts = ROOT / "facts.md"
    if facts.exists():
        for match in re.finditer(r"^-\s*\[[A-Z]+\d+\]\s*(.+?)(?:\s*\|\s*tags:.*)?$",
                                 facts.read_text(encoding="utf-8"), re.M):
            if len(match.group(1)) >= FACT_LENGTH:
                found.append(match.group(1).strip())
    answers = ROOT / "answers.md"
    if answers.exists():
        section = ""
        for line in answers.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                section = line
            elif line.startswith("- ") and ":" in line and "Defaults" not in section:
                found.append(line.split(":", 1)[1].strip())
    # The collected Comeet tokens belong to the companies, and the links applied to say where he applied.
    companies = ROOT / "companies.json"
    if companies.exists():
        for _, board, token in json.loads(companies.read_text(encoding="utf-8")):
            if board == "comeet":
                found += token.split(":")
    database = ROOT / "job_agent.db"
    if database.exists():
        with sqlite3.connect(database) as connection:
            try:
                found += [row[0] for row in connection.execute("SELECT url FROM applications WHERE url IS NOT NULL")]
            except sqlite3.Error:
                pass
    profile = ROOT / "profile.md"
    if profile.exists():
        personal = re.split(r"^## Rubric", profile.read_text(encoding="utf-8"), flags=re.M)[0]
        found += [line[2:].strip() for line in personal.splitlines() if line.startswith("- ")
                  and len(line) >= FACT_LENGTH]
    return sorted({s for s in found if len(s) >= MIN_LENGTH}, key=len, reverse=True)


def private_terms() -> list[re.Pattern]:
    """Words that identify the candidate anywhere they appear, even inside a comment.

    The name is read from CANDIDATE_NAME, and anything else, a former employer, a school,
    a unit, comes from PRIVATE_TERMS in .env, comma separated. Matched as whole words and
    case-insensitively, because an employer's name in a comment is as public as in a prompt.
    """
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    words = env.get("CANDIDATE_NAME", "").split() + env.get("PRIVATE_TERMS", "").split(",")
    return [re.compile(r"(?<![\w֐-׿])" + re.escape(w.strip()) + r"(?![\w֐-׿])", re.I)
            for w in words if len(w.strip()) >= 3]


def scan(folder: pathlib.Path) -> list[str]:
    private = secrets()
    terms = private_terms()
    problems = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(folder)
        for value in private:
            if value in text:
                problems.append(f"{rel}: contains private text: {value[:50]!r}")
        for term in terms:
            for match in term.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{rel}:{line}: names the candidate: {match.group(0)!r}")
        for match in PHONE.finditer(text):
            if match.group(0) != "050-0000000":
                problems.append(f"{rel}: phone-like number {match.group(0)}")
        for match in EMAIL.finditer(text):
            if not ALLOWED_EMAILS.search(match.group(0)):
                problems.append(f"{rel}: email address {match.group(0)}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the public copy and check it for private data.")
    parser.add_argument("--out", default=str(ROOT.parent / "job-agent-public"))
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    out = pathlib.Path(args.out).resolve()
    if out == ROOT or ROOT in out.parents:
        print("the public copy must be outside this folder")
        return 1
    if out.exists():
        # Only a folder this script built before is refreshed, never an arbitrary one.
        if not (out / ".public-copy").exists():
            print(f"{out} exists and was not built by publish.py, choose another --out")
            return 1
        # Everything is replaced except the git repository, whose history is the whole point.
        for child in out.iterdir():
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()

    missing = [name for name in PUBLIC if not (ROOT / name).exists()]
    if missing:
        print("missing from the project: " + ", ".join(missing))
        return 1
    out.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC:
        shutil.copy2(ROOT / name, out / name)
    (out / ".public-copy").write_text("built by publish.py\n", encoding="utf-8")

    problems = scan(out)
    if problems:
        print(f"NOT SAFE TO PUBLISH, {len(problems)} problems in {out}:")
        for problem in problems:
            print("  " + problem)
        return 1
    print(f"built {out}: {len(PUBLIC)} files, no private data found")
    print("read it before pushing: git init, git add ., git status, then look at every file listed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
