"""The fixed shape of the CV, copied from the candidate's own CV.

The model only chooses content: which headline, which summary lines, which bullets
under each project and in which order, and whether experience or projects comes
first. Everything structural stays as it is in the original: the order of sections, a
header line above each project and above each role, and skills grouped under
named categories. Leaving the structure to the model is what produced CVs with no
project names, no role line and a single run-on list of skills.

The preview page, the text the audit reads and the Word file are all built from the
one structure returned here, so what the candidate reads is what gets checked and sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADER = re.compile(r"^[A-Z]H\d+$")

# Skill categories and their order, as in the original CV. Display text is the fact's own words,
# except where the fact carries a qualifier meant for the model rather than the reader.
SKILL_GROUPS = [
    ("Languages", ["K1", "K3", "K2"]),
    ("Backend", ["K4", "K5", "K6", "K7"]),
    ("Frontend", ["K16"]),
    ("Databases", ["K8", "K18"]),
    ("Testing", ["K9"]),
    ("Infrastructure & Tools", ["K10", "K11", "K12", "K13"]),
    ("Networking", ["K15"]),
    ("AI and LLM Work", ["K19", "K17"]),
]
# Printed for every job, whatever the selection, as the core a recruiter looks for first.
# Everything else appears only when the selection names it, so the list reads as what the
# candidate can be interviewed on for this role rather than everything ever touched.
# K14, cloud platforms known from the support side, is kept as a fact but never listed:
# in a skills list it reads as hands-on building, and the summary already says it right.
CORE_SKILLS = {"K1", "K4", "K8", "K12", "K10"}
SKILL_DISPLAY = {
    "K13": "Linux",
    # The long forms are written for the model to judge; the skills line needs the short ones.
    "K17": "Claude Code and Gemini for scaffolding, refactoring and code review",
    "K19": "Structured model output against a schema, prompt design, independent verification pass",
}

# Each project's bullets share the letter of its header id; the first bullet introduces it.
PROJECTS = [("P", "PH1", "P1"), ("J", "JH1", "J1"), ("A", "AH1", "A1"), ("C", "CH1", "C1")]
EXPERIENCE_HEADER = "ZH1"
SERVICE_HEADER = "VH1"

# As many bullets as the original CV carries in each place, which is what keeps it to one page.
MAX_BULLETS = {"P": 4, "J": 3, "A": 2, "C": 1, "Z": 4}
BULLET_BUDGET = 12


@dataclass
class Block:
    header: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class Layout:
    headline: str
    summary: str
    skills: list[tuple[str, str]]
    education: list[str]
    projects: list[Block]
    experience: list[Block]
    order: list[str]  # section names in print order after the summary


MAX_COURSES = 4


def education_lines(chosen_ids: list[str], facts: dict[str, str]) -> list[str]:
    """The degree, then the courses chosen for this posting on one line, then languages.

    Only G lines can appear as courses, and facts.md holds only courses graded 90 or above,
    so a weak grade can never reach the page. With no course chosen, the line is left out.
    """
    courses = [facts[i] for i in chosen_ids if i.startswith("G") and i in facts][:MAX_COURSES]
    lines = [facts["E1"]] if "E1" in facts else []
    if courses:
        lines.append("Selected coursework: " + ", ".join(courses) + ".")
    if "E3" in facts:
        lines.append(facts["E3"])
    return lines


def base_id(fact_id: str) -> str:
    """P5a and P5 are two wordings of one fact, and share the base P5."""
    return fact_id.rstrip("abcdefghijklmnopqrstuvwxyz")


def one_wording(ids: list[str]) -> list[str]:
    """Keep the first wording chosen of each fact, so one claim never appears twice in other words."""
    seen: set[str] = set()
    out = []
    for fact_id in ids:
        if base_id(fact_id) not in seen:
            seen.add(base_id(fact_id))
            out.append(fact_id)
    return out


def build(chosen, facts: dict[str, str]) -> Layout:
    """Turn a selection into the fixed layout. Works on the pydantic model or its dict form."""
    get = (lambda k: getattr(chosen, k)) if hasattr(chosen, "headline_id") else chosen.get

    wanted = CORE_SKILLS | set(get("skill_ids") or [])
    skills = []
    for name, ids in SKILL_GROUPS:
        texts = [SKILL_DISPLAY.get(i, facts[i]) for i in ids if i in facts and i in wanted]
        if texts:
            skills.append((name, ", ".join(texts)))

    picked = one_wording([i for i in get("project_ids") if i in facts and not HEADER.match(i)])
    letters = [letter for letter, _, _ in PROJECTS]
    # Projects appear in the order the model first mentions them; any it skipped keep their CV order.
    ranked = sorted(letters, key=lambda l: next((n for n, i in enumerate(picked) if i.startswith(l)), 99 + letters.index(l)))
    projects = []
    for letter in ranked:
        _, header_id, intro_id = next(p for p in PROJECTS if p[0] == letter)
        bullets = [i for i in picked if i.startswith(letter) and not i.startswith(letter + "H")]
        # Only the main project is printed unasked. With four projects and one page, the others
        # appear when the selection wanted them.
        if not bullets and letter != "P":
            continue
        # The introducing line always leads, so a reader knows what the project is before its details.
        intro = next((i for i in bullets if base_id(i) == intro_id), intro_id)
        bullets = ([intro] + [i for i in bullets if base_id(i) != intro_id])[:MAX_BULLETS[letter]]
        projects.append(Block(facts.get(header_id, ""), [facts[i] for i in bullets if i in facts]))

    role = [i for i in get("experience_ids") if i.startswith("Z") and i in facts and not HEADER.match(i)]
    # The line that says what the role was leads, as a project's first line does.
    role = [i for i in role if i == "Z1"] + [i for i in role if i != "Z1"]
    experience = [Block(facts.get(EXPERIENCE_HEADER, ""), [facts[i] for i in role[:MAX_BULLETS["Z"]]])]
    # Military service is one line that every Israeli recruiter looks for, so it is always printed.
    if SERVICE_HEADER in facts:
        experience.append(Block(facts[SERVICE_HEADER], [facts[i] for i in ("V1",) if i in facts]))

    # One page is the whole point, and the page holds about this many bullets. When a selection
    # asks for more, the previous role gives up lines first, down to two, since the projects carry the
    # development case; then the longest project gives one up at a time, never emptying one.
    blocks = projects + experience
    while sum(len(b.bullets) for b in blocks) > BULLET_BUDGET:
        if len(experience[0].bullets) > 2:
            experience[0].bullets.pop()
            continue
        longest = max(projects, key=lambda b: len(b.bullets))
        if len(longest.bullets) <= 1:
            break
        longest.bullets.pop()

    # Projects before experience every time, since support roles left the search on 2026-09-18
    # and the previous role no longer leads for any job. lead_with is kept only so old drafts load.
    order = ["skills", "education", "projects", "experience"]

    return Layout(
        headline=facts.get(get("headline_id"), ""),
        summary=" ".join(facts[i] for i in one_wording(get("summary_ids")) if i in facts and i.startswith("S")),
        skills=skills,
        education=education_lines(get("education_ids") or [], facts),
        projects=projects,
        experience=experience,
        order=order,
    )


TITLES = {"skills": "Technical Skills", "education": "Education", "projects": "Projects",
          "experience": "Professional Experience"}


def as_text(layout: Layout, cover_note: str) -> str:
    """The plain-text version the audit reads, with the same sections and headers as the files."""
    lines = [layout.headline, "", "PROFESSIONAL SUMMARY", layout.summary, ""]
    for name in layout.order:
        lines.append(TITLES[name].upper())
        if name == "skills":
            lines += [f"{label}: {text}" for label, text in layout.skills]
        elif name == "education":
            lines += layout.education
        else:
            for block in getattr(layout, name):
                lines.append(block.header)
                lines += [f"- {b}" for b in block.bullets]
        lines.append("")
    lines += ["COVER NOTE", cover_note]
    return "\n".join(lines)
