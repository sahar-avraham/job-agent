"""Drop jobs that fail hard rules, before any of them reaches a paid model call.

This is the cheap gate in the pipeline. Every rule here is a plain string or
number test with a definite answer, so nothing that needs an opinion belongs in
this file. Its real job is cost: it typically removes most of the list, and only
the survivors are worth scoring.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter
from datetime import date

def phrases(words: list[str]) -> re.Pattern:
    """Match whole words only, because a bare substring made "iv" hit "IVR" and "intern" hit "internal"."""
    return re.compile(r"(?<![\w֐-׿])(" + "|".join(re.escape(w) for w in words) + r")(?![\w֐-׿])", re.I)


# Titles containing any of these are dropped, because they ask for a level you do not have yet.
SENIORITY_BLOCKLIST = [
    "senior", "sr", "sr.", "staff", "principal", "lead", "head of", "director",
    "vp", "vice president", "manager", "architect", "expert", "chief", "leader", "team leader",
    "iii", "iv", "10x", "team lead", "tech lead", "group leader", "בכיר", "בכירה", "ראש צוות",
]

# Titles containing any of these are dropped, because the role is not technical at all.
ROLE_BLOCKLIST = [
    "sales", "account executive", "recruiter", "marketing", "customer success",
    "legal", "counsel", "finance", "controller", "hr", "people partner",
    "office manager", "executive assistant", "business development",
    "business partner", "finops", "consultant", "fp&a", "procurement", "buyer",
    "business analyst", "business data analyst", "financial analyst", "data analyst",
    "big data analyst", "compliance analyst", "product designer", "technical artist",
    "presales", "pre-sales", "implementation",
    # Found scoring 12 to 29 on 2026-09-30, among the first Workday jobs.
    "pre-sale", "presale", "customer service engineer", "clinical", "regulatory",
]

# Technical roles that scored low every time, dropped on 2026-09-18:
# testing (9 scored, none reached 50), verification and field roles (6, none), security research (5, none).
LOW_SCORING_BLOCKLIST = [
    "qa", "quality", "test", "tester", "testing", "בודק", "בודקת", "בדיקות", "בדיקה", "איכות", "ולידציה", "ניסויים",
    "verification", "validation", "field", "project engineer", "vlsi",
    "security researcher", "vulnerability researcher", "malware researcher", "threat researcher",
]

# Engineering that is not software. "Engineer" alone let mechanical and hardware roles through.
DISCIPLINE_BLOCKLIST = [
    "mechanical", "electrical", "electronic", "electronics", "hardware", "configuration control", "optical", "optics", "rf",
    "analog", "asic", "fpga", "pcb", "chip", "silicon", "physical design", "layout",
    "mechatronics", "biomedical", "chemical", "civil", "manufacturing", "process engineer",
    "npi", "packaging", "thermal", "materials", "technician", "technicians",
    # Abbreviations the first list missed, found scoring 1 to 7 in batch 4.
    "hw", "ate", "qc", "failure analysis", "labview",
    # The same disciplines in Hebrew, which came with Elbit's titles once Hebrew engineers passed.
    "מכונות", "מכני", "מכנית", "כימיה", "חשמל", "אלקטרוניקה", "תהליך", "תפי", "תפ\"י", "תעו\"נ",
    "ייצור", "יצור", "חומרה", "טכנאי", "הנדסאי", "אופטיקה",
    # Chip design, firmware and manufacturing titles from Workday, 2026-09-30: 24 scored across all
    # jobs so far and none reached 50, the highest a firmware role at 43.
    "firmware", "rtl", "dft", "sta", "circuit", "board design", "logic design", "signal integrity",
    "power integrity", "physical layer", "soc clock", "soc clocks", "design automation",
    "mfg", "industrial engineer", "opex",
]

# A title must contain one of these to survive. Hebrew developer titles are here because
# a Java role at Discount Bank was dropped for being written in Hebrew.
TITLE_ALLOWLIST = [
    "engineer", "engineering", "developer", "software", "backend", "back end", "full stack",
    "fullstack", "frontend", "front end", "java", "python", "devops", "devsecops", "sre",
    "infrastructure", "platform", "cloud", "reliability", "security", "support",
    "מפתח", "מפתחת", "מפתח.ת", "מפתח/ת", "תוכניתן", "תוכניתנית", "תמיכה",
    # Elbit writes most titles in Hebrew, and its software and systems engineers were dropped, 2026-10-08.
    "מהנדס", "מהנדסת",
]

# Jobs demanding more than this many years of experience are dropped.
MAX_YEARS_REQUIRED = 3

# Postings older than this are dropped, and ones older than OLD_DAYS are marked on the page.
STALE_DAYS = 183
OLD_DAYS = 90

# Support roles are dropped altogether, decided on 2026-09-18 after first keeping
# the ones that involve code. Escalation and NOC work are the same job under another name.
SUPPORT_TITLES = re.compile(r"\bsupport\b|\bhelp ?desk\b|\btier \d\b|\bescalation\b|\bnoc\b|תמיכה", re.I)

# Roles for students, where the CV and letter say the degree is completed but not yet conferred.
STUDENT_ROLE = re.compile(r"\bstudent\b|\bintern\b|\binternship\b|סטודנט", re.I)

# The commute area is personal, so it lives in commute.json, kept out of git like companies.json;
# commute.example.json shows its shape. A job is dropped only when its location names a place outside
# the area and no place inside it, so "Israel", "Remote" or "Tel Aviv, Jerusalem" all stay. Without the
# file no job is dropped for its place.
COMMUTE_FILE = pathlib.Path(__file__).resolve().parent / "commute.json"


def load_commute() -> tuple[list[str], list[str]]:
    """Read the places inside and outside the commute area, as (inside, outside)."""
    if not COMMUTE_FILE.exists():
        return [], []
    area = json.loads(COMMUTE_FILE.read_text(encoding="utf-8"))
    return area.get("inside", []), area.get("outside", [])


INSIDE_AREA, OUTSIDE_AREA = load_commute()
OUTSIDE = phrases(OUTSIDE_AREA) if OUTSIDE_AREA else None
INSIDE = phrases(INSIDE_AREA) if INSIDE_AREA else None

# Jobs at these companies are dropped regardless of anything else.
COMPANY_BLOCKLIST: list[str] = []

# A junior word in the title marks the role itself. In the description it usually does not,
# because "mentor junior engineers" is a line in senior postings, so only unambiguous phrases count there.
JUNIOR_TITLE_WORDS = ["junior", "jr", "entry level", "entry-level", "graduate", "student", "intern",
                      "internship", "new grad", "ג'וניור", "סטודנט", "סטודנטית", "מתחיל", "מתחילה"]
JUNIOR_DESCRIPTION = re.compile(
    r"\b(entry[- ]level|new grads?|recent (?:cs |computer science )?graduates?"
    r"|no (?:prior |previous )?experience (?:is )?required|student position|graduates? welcome)\b"
    r"|ללא ניסיון|בוגרי תואר טרי", re.I)

SENIORITY = phrases(SENIORITY_BLOCKLIST)
ROLES = phrases(ROLE_BLOCKLIST)
DISCIPLINES = phrases(DISCIPLINE_BLOCKLIST)
LOW_SCORING = phrases(LOW_SCORING_BLOCKLIST)
ALLOWED = phrases(TITLE_ALLOWLIST)
JUNIOR_TITLE = phrases(JUNIOR_TITLE_WORDS)
# Analysts pass only for security analysis and threat hunting, the one analyst kind wanted.
SECURITY_ANALYST = re.compile(
    r"\b(security|threat|soc|secops|cyops|dfir|cyber)\b.*\banalyst\b"
    r"|\banalyst\b.*\b(security|threat|cyber)\b|\bthreat hunt", re.I)

# Postings often spell the number out, and "at least one year" was read as no demand at all.
# Converted only where the words frame a requirement, because "two years in a row" is boilerplate.
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_NUMBER = r"\b(" + "|".join(NUMBER_WORDS) + r")\b(?=\s*(?:\(\d+\)\s*)?\+?\s*years?"
NUMBER_PATTERN = re.compile(
    r"(?<=at least )" + _NUMBER + r")|(?<=minimum of )" + _NUMBER + r")"
    r"|" + _NUMBER + r"(?:\s+of)?(?:\s+[\w-]+){0,3}\s+experience)", re.I)
HEBREW_WORDS = re.compile(r"(ניסיון של |לפחות |מינימום )(שנתיים|שנה)")

YEARS_PATTERNS = [
    # "5+ years of experience", "3-5 years experience", "1~2 years of proven experience".
    re.compile(r"(\d{1,2})\s*(?:\+|[-~]\s*\d{1,2})?\s*years?[^.\n]{0,70}?experience", re.I),
    # "experience: at least 4 years", "minimum of 2 years".
    re.compile(r"\bexperience\b[^.\n]{0,70}?(\d{1,2})\s*\+?\s*years?", re.I),
    # "1-3 years in technical support". Many postings never use the word experience,
    # and requiring it missed the demand entirely.
    re.compile(r"(\d{1,2})\s*(?:\+|[-~]\s*\d{1,2})?\s*years?\s+(?:of\s+)?(?:in|with|working|hands|as)\b", re.I),
    re.compile(r"(?:at least|minimum of|over)\s+(\d{1,2})\s*years?", re.I),
    # Hebrew postings: "ניסיון של 3 שנים", "3 שנות ניסיון", "לפחות 4 שנים".
    re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?שנ(?:ות|ים)"),
    # "5+ years of production Python", where the word experience never appears.
    re.compile(r"(\d{1,2})\s*\+?\s*(?:[-–~]\s*\d{1,2}\s*)?years?\s+of\s+(?:[\w/+#.-]+\s+){0,3}", re.I),
    # "5+ years building security solutions", "5+ years running production".
    re.compile(r"(\d{1,2})\s*\+?\s*(?:[-–~]\s*\d{1,2}\s*)?years?\s+(?:of\s+)?"
               r"(?:building|developing|running|designing|leading|writing|managing|shipping|operating)\b", re.I),
]

# Where the requirements start. Figures before it are usually about the company, not the candidate.
REQUIREMENTS = re.compile(
    r"requirements|qualifications|what you bring|what you.ll bring|who you are|about you|you have|you bring"
    r"|what we.re looking for|what we look for|must have|דרישות|מה אנחנו מחפשים", re.I)
# Where the optional part starts, and the words that make a single line optional.
OPTIONAL_SECTION = re.compile(r"nice to have|advantages?\s*:|bonus points|preferred qualifications|יתרונות|יתרון\s*:", re.I)
OPTIONAL_LINE = re.compile(r"nice to have|advantage|bonus|preferred|a plus|desirable|יתרון", re.I)
LINE_BREAKS = ".\n•·"


def days_posted(job: dict) -> int | None:
    """Count the days since the board says the job was posted, or None when it gives no date."""
    try:
        return (date.today() - date.fromisoformat((job.get("posted") or "")[:10])).days
    except ValueError:
        return None


def required_years(text: str) -> int | None:
    """Return the highest experience figure the requirements insist on, or None when they state none.

    Every figure in a requirements list is a demand the candidate must meet, so the job asks
    for the largest of them: "8+ years of backend, including 2 in Python" is an eight-year
    job, and so is "1+ year with LLMs, 5+ years of backend". Lines marked as an advantage are
    skipped. Checked against 126 scored jobs this drops 26 more of them, none scoring 50 or above.
    """
    text = NUMBER_PATTERN.sub(lambda m: NUMBER_WORDS[m.group(0).lower()], text or "")
    text = HEBREW_WORDS.sub(lambda m: m.group(1) + ("2 שנים" if m.group(2) == "שנתיים" else "1 שנים"), text)
    found = []
    for pattern in YEARS_PATTERNS:
        for match in pattern.finditer(text):
            value = int(match.group(1))
            # Ignore absurd matches such as a company founded 20 years ago.
            if 0 < value <= 15:
                found.append((match.start(1), value))
    if not found:
        return None

    section = REQUIREMENTS.search(text)
    start = section.start() if section else 0
    optional = OPTIONAL_SECTION.search(text, start)
    end = optional.start() if optional else len(text)
    demanded = []
    for position, value in found:
        if not start <= position < end:
            continue
        line_start = max(text.rfind(mark, 0, position) for mark in LINE_BREAKS)
        line_end = min([i for i in (text.find(mark, position) for mark in LINE_BREAKS) if i != -1] or [len(text)])
        if not OPTIONAL_LINE.search(text[line_start + 1:line_end]):
            demanded.append(value)
    if demanded:
        return max(demanded)
    # Nothing insisted on inside the requirements, so fall back to the first figure after they start.
    after = sorted(f for f in found if f[0] >= start) or sorted(found)
    return after[0][1]


# "Master" alone would catch "Scrum Master" and "master data", so it counts only as a degree.
ADVANCED_DEGREE = re.compile(
    r"\b(m\.\s?sc\.?|msc|ph\.?\s?d|doctorate|master'?s?\s+(?:degree|in|of)|graduate degree)(?![\w])|תואר שני|דוקטורט", re.I)
FIRST_DEGREE_OR_WAIVER = re.compile(
    r"\b(b\.?\s?sc|bachelor'?s?|b\.?\s?a|ba|bs|b\.?\s?tech|b\.?e|equivalent)\b|תואר ראשון|או ניסיון", re.I)


def requires_advanced_degree(text: str) -> bool:
    """Say whether the requirements insist on a master's or doctorate, as opposed to preferring one.

    Decided on 2026-09-18 to drop only a hard demand: "M.Sc. preferred" or "B.Sc. or M.Sc."
    is still worth applying to, so a line that also accepts a first degree or an equivalent,
    or that marks the degree as an advantage, does not count.
    """
    text = text or ""
    section = REQUIREMENTS.search(text)
    start = section.start() if section else 0
    optional = OPTIONAL_SECTION.search(text, start)
    end = optional.start() if optional else len(text)
    for match in ADVANCED_DEGREE.finditer(text, start, end):
        # A short window rather than the whole line, because many feeds arrive with no line breaks at all.
        line_start = max([text.rfind(mark, 0, match.start()) for mark in "\n•·"] + [match.start() - 40])
        line_end = min([i for i in (text.find(mark, match.end()) for mark in "\n•·") if i != -1] + [match.end() + 80])
        line = text[max(line_start + 1, 0):line_end]
        if not OPTIONAL_LINE.search(line) and not FIRST_DEGREE_OR_WAIVER.search(line):
            return True
    return False


# The trailing dot of "B.Sc." is part of the match, or the line would stop right after the abbreviation.
DEGREE_LINE = re.compile(r"(b\.?\s?sc\.?|bachelor|degree|תואר|m\.?\s?sc\.?|practical engineer|הנדסאי)[^.\n•·]{0,90}", re.I)
OTHER_FIELD = re.compile(
    r"electrical|electronics?|mechanical|aerospace|aeronautic|physics|chemical|biomedical|industrial|materials"
    r"|systems engineering|חשמל|אלקטרוניקה|מכונות|תעשייה וניהול", re.I)
COMPUTING_OR_OPEN = re.compile(
    r"computer|software|\bcs\b|related|equivalent|similar|relevant|technical field|מדעי המחשב|הנדסת תוכנה|רלוונטי", re.I)


def requires_other_degree(text: str) -> bool:
    """Say whether the requirements insist on an engineering degree other than computing.

    Added on 2026-09-18: seven scored jobs demanded one and none
    reached 15. A line that also accepts computer science, a related field or an equivalent,
    or that marks the degree as an advantage, does not count.
    """
    # Drop the dots inside degree abbreviations, which otherwise end the line mid-requirement.
    text = re.sub(r"\b([bm])\.\s?sc\.?", r"\1sc", text or "", flags=re.I)
    text = re.sub(r"\bph\.\s?d\.?", "phd", text, flags=re.I)
    section = REQUIREMENTS.search(text)
    start = section.start() if section else 0
    optional = OPTIONAL_SECTION.search(text, start)
    end = optional.start() if optional else len(text)
    for match in DEGREE_LINE.finditer(text, start, end):
        line = match.group(0)
        if OTHER_FIELD.search(line) and not COMPUTING_OR_OPEN.search(line) and not OPTIONAL_LINE.search(line):
            return True
    return False


def rejection_reason(job: dict, max_years: int) -> str | None:
    """Return why this job is dropped, or None when it survives every rule."""
    title = job.get("title", "")
    company = job.get("company", "").lower()
    description = job.get("description", "")

    if any(name.lower() in company for name in COMPANY_BLOCKLIST):
        return "company blocked"
    if (age := days_posted(job)) is not None and age > STALE_DAYS:
        return "posted over six months ago"
    if match := ROLES.search(title):
        return f"non-engineering title ({match.group(1).lower()})"
    if match := SENIORITY.search(title):
        return f"too senior ({match.group(1).lower()})"
    if match := DISCIPLINES.search(title):
        return f"not software ({match.group(1).lower()})"
    if match := LOW_SCORING.search(title):
        return f"role that scores low ({match.group(1).lower()})"
    if not ALLOWED.search(title) and not SECURITY_ANALYST.search(title):
        return "title outside target roles"

    if SUPPORT_TITLES.search(title) and not SECURITY_ANALYST.search(title):
        return "support role"
    location = job.get("location") or ""
    if OUTSIDE and (match := OUTSIDE.search(location)) and not (INSIDE and INSIDE.search(location)):
        return f"outside the commute area ({match.group(1).lower()})"
    if requires_advanced_degree(description):
        return "requires a master's or doctorate"
    if requires_other_degree(description):
        return "requires a non-computing engineering degree"

    if JUNIOR_TITLE.search(title) or JUNIOR_DESCRIPTION.search(description):
        return None

    years = required_years(description)
    if years is not None and years > max_years:
        return f"wants {years}+ years"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply hard rules before any model call.")
    parser.add_argument("--jobs", default="jobs.json", help="input produced by fetch_jobs.py --json")
    parser.add_argument("--out", default="filtered.json", help="where to write the surviving jobs")
    parser.add_argument("--max-years", type=int, default=MAX_YEARS_REQUIRED, help="highest experience demand you accept")
    parser.add_argument("--explain", action="store_true", help="also list every dropped job with its reason")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    path = pathlib.Path(args.jobs)
    if not path.exists():
        print(f"missing {path}, run: python fetch_jobs.py --descriptions --json {path}", file=sys.stderr)
        return 1

    jobs = json.loads(path.read_text(encoding="utf-8"))
    kept, dropped = [], []
    for job in jobs:
        reason = rejection_reason(job, args.max_years)
        (dropped if reason else kept).append((job, reason))

    for job, _ in kept:
        print(f"KEEP  {job['company']:<12} {job['title'][:60]}")
    if args.explain:
        print()
        for job, reason in dropped:
            print(f"DROP  {reason:<28} {job['company']:<12} {job['title'][:48]}")

    print()
    print(f"kept {len(kept)} of {len(jobs)}")
    for reason, count in Counter(reason for _, reason in dropped).most_common():
        print(f"  {count:>4}  {reason}")

    pathlib.Path(args.out).write_text(
        json.dumps([job for job, _ in kept], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
