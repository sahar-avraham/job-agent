"""The page about where jobs come from: what the last collection read, and which boards failed.

A company's board is read by its address in companies.json. When a company moves to another
system the old address starts failing, and without this page it would only be one line in a run's
output. Served by serve.py at /sources, next to the jobs and the tracking pages.
"""

from __future__ import annotations

import collections
import html
import urllib.parse
from datetime import datetime

import fetch_jobs
import report
import store

BOARD_LABELS = {"comeet": "Comeet", "greenhouse": "Greenhouse", "workday": "Workday", "ashby": "Ashby", "lever": "Lever",
                "workable": "Workable", "smartrecruiters": "SmartRecruiters",
                "amazon": "Amazon", "eightfold": "Eightfold",
                "elbit": "Elbit", "bob": "Bob", "oracle": "Oracle"}

CSS = """
.sources table { margin-bottom:1.6rem; }
.fail-link { font-size:.8rem; }
"""


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def day(stamp: str | None) -> str:
    return datetime.fromisoformat(stamp).astimezone().strftime("%d/%m %H:%M") if stamp else ""


def scout_state(row: dict) -> str:
    """Say in a few words what the scout found for one company."""
    if row["status"] == "added":
        return f'נוספה אוטומטית, <span class="ltr">{BOARD_LABELS.get(row["system"], esc(row["system"]))}</span>'
    note = row.get("note") or ""
    if note.startswith("uses "):
        why = f'משתמשת ב־<span class="ltr">{esc(note[5:])}</span>, שאין לה מתאם'
    elif note.startswith("page did not open"):
        why = "דף הקריירה לא נפתח"
    elif "lists no jobs in Israel" in note:
        why = "המערכת שלה לא מציגה משרות בארץ"
    elif note.startswith("no careers page"):
        why = "אין דף קריירה במפה"
    else:
        why = "לא זוהתה מערכת גיוס"
    return f"נקראת מהמפה: {why}"


def scout_block(rows: list[dict]) -> str:
    if not rows:
        return ""
    added = sum(1 for r in rows if r["status"] == "added")
    lines = "".join(
        f'<tr><td class="ltr">{esc(r["company"])}</td><td class="num">{r["jobs"]}</td><td>{scout_state(r)}</td>'
        + (f'<td><a class="fail-link" target="_blank" rel="noopener" href="{esc(r["careers"])}">דף הקריירה</a></td>'
           if r.get("careers") else "<td></td>") + "</tr>"
        for r in rows)
    return ('<h2>גילוי חברות שמגייסות בתחום שלך</h2>'
            '<p class="meta">חברות שמפת ההייטק מציגה אצלן משרות בתחום שלך, ושאף לוח לא קרא לפני שהתגלו. '
            f'{added} נוספו אוטומטית לסריקה. את המשרות של השאר קוראים מהמפה עצמה, בלי תיאור.</p>'
            '<div class="wide-table"><table><thead><tr><th>חברה</th><th>משרות במפה</th><th>מצב</th><th></th>'
            f'</tr></thead><tbody>{lines}</tbody></table></div>')


def render(runs: list[dict], failures: list[dict], open_by_company: dict[str, int],
           workable_companies: set[str] = frozenset(), scout_rows: list[dict] = ()) -> str:
    last = runs[0] if runs else None
    per_board = collections.Counter(board for _, board, _ in fetch_jobs.COMPANIES)
    # A company counts as hiring here when the latest collection found at least one of its jobs in Israel.
    # Workable is one entry for all its customers, so its companies are counted from the jobs themselves.
    hiring = collections.Counter(board for company, board, _ in fetch_jobs.COMPANIES
                                 if board != "workable" and open_by_company.get(company))
    hiring["workable"] = len(workable_companies)
    if "workable" in per_board:
        per_board["workable"] = max(per_board["workable"], len(workable_companies))
    board_rows = "".join(
        f'<tr><td>{BOARD_LABELS.get(board, esc(board))}</td><td class="num">{count}</td>'
        f'<td class="num">{hiring[board]}</td></tr>'
        for board, count in per_board.most_common())

    fail_rows = "".join(
        f'<tr><td class="ltr">{esc(f["company"])}</td><td>{BOARD_LABELS.get(f["board"], esc(f["board"]))}</td>'
        f'<td class="num">{day(f["first_failed"])}</td><td class="ltr">{esc(f["error"])}</td>'
        f'<td><a class="fail-link" target="_blank" rel="noopener" href="https://www.google.com/search?q='
        f'{urllib.parse.quote(f["company"] + " careers jobs")}">חפש לאן עברו</a></td></tr>'
        for f in failures)
    failures_section = (
        '<h2>אתרים שלא נקראו</h2>'
        '<p class="meta">כתובת שנכשלת בדרך כלל אומרת שהחברה עברה למערכת אחרת או סגרה את אתר המשרות. '
        'האתר נשאר כאן עד שהוא נקרא שוב בהצלחה.</p>'
        '<div class="wide-table"><table><thead><tr><th>חברה</th><th>מערכת</th><th>נכשל מאז</th><th>שגיאה</th><th></th>'
        f'</tr></thead><tbody>{fail_rows}</tbody></table></div>'
    ) if failures else '<h2>אתרים שלא נקראו</h2><p class="meta">כל האתרים נקראו בהצלחה באיסוף האחרון.</p>'

    run_rows = "".join(
        f'<tr><td class="num">{day(r["started_at"])}</td><td class="num">{r["collected"]}</td>'
        f'<td class="num">{r["new_jobs"]}</td><td class="num">{r["kept"]}</td><td class="num">{r["scored"]}</td></tr>'
        for r in runs)
    last_line = (f'<p class="meta">האיסוף האחרון: {day(last["started_at"])}, {last["collected"]} משרות בישראל, '
                 f'{last["new_jobs"]} חדשות.</p>') if last else '<p class="meta">עוד לא היה איסוף.</p>'

    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>סריקת משרות</title><style>{report.CSS}{CSS}</style></head>
<body><div class="doc sources">
<nav class="pages"><a href="/">משרות</a><a href="/tracking">מעקב הגשות</a><a href="/sources" class="here">סריקת משרות</a></nav>
<header>
  <h1>סריקת משרות</h1>
  {last_line}
</header>

{failures_section}

{scout_block(list(scout_rows))}

<h2>חברות לפי מערכת</h2>
<p class="meta">כמה חברות נבדקות בכל הרצה, וכמה מהן היו עם משרה פתוחה בישראל באיסוף האחרון.</p>
<table><thead><tr><th>מערכת</th><th>חברות</th><th>עם משרות בישראל</th></tr></thead>
<tbody>{board_rows}</tbody></table>

<h2>הרצות אחרונות</h2>
<div class="wide-table"><table><thead><tr><th>מתי</th><th>נאספו</th><th>חדשות</th><th>עברו סינון</th><th>דורגו</th></tr></thead>
<tbody>{run_rows}</tbody></table></div>
</div></body></html>"""


def page(db: str) -> str:
    connection = store.connect(db)
    try:
        runs = [dict(r) for r in connection.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 10")]
        latest = connection.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
        open_by_company = dict(connection.execute(
            "SELECT company, COUNT(*) FROM jobs WHERE last_seen = ? GROUP BY company", (latest,)).fetchall())
        workable = {row[0] for row in connection.execute(
            "SELECT DISTINCT company FROM jobs WHERE last_seen = ? AND url LIKE '%jobs.workable.com%'", (latest,))}
        scout_rows = [dict(r) for r in connection.execute(
            "SELECT * FROM scout WHERE jobs > 0 ORDER BY status = 'added' DESC, jobs DESC, company")]
        return render(runs, store.board_failures(connection), open_by_company, workable, scout_rows)
    finally:
        connection.close()
