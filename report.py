"""Turn the database into one page you can apply from.

This is the end of the pipeline. Everything before it exists so that this page
holds the few jobs worth your attention, with the reasoning attached, and nothing
else you have to open.

Hebrew chrome around English content, with every English block isolated to its own
text direction. Mixing the two inside one line is what makes a bilingual page
unreadable, so the two never share a line here.
"""

from __future__ import annotations

import argparse
import collections
import html
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import filter_jobs
import keywords
import settings
import store

CSS = """
:root {
  --paper:#f6f7f6; --surface:#fff; --ink:#16211f; --soft:#55665f; --faint:#8b9a94;
  --rule:#dde3e0; --accent:#0d6a66; --accent-soft:#e2eeec;
  --good:#2f6b3c; --good-soft:#e4efe5; --warn:#a8442a; --warn-soft:#f6e8e3;
  --shadow:0 1px 2px rgba(22,33,31,.06),0 8px 24px rgba(22,33,31,.05);
  --code-bg:#eef1f0; --code-fg:#16211f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper:#10161a; --surface:#161e22; --ink:#e6ecea; --soft:#9fb0ab; --faint:#6d7f7a;
    --rule:#26333a; --accent:#63c9bf; --accent-soft:#152c2b;
    --good:#8cc596; --good-soft:#17251a; --warn:#e2977c; --warn-soft:#2b1d18;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
    --code-bg:#1c2629; --code-fg:#e6ecea;
  }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.65 "Segoe UI",system-ui,sans-serif; }
.doc { direction:rtl; text-align:right; max-width:56rem; margin:0 auto;
  padding:clamp(1.5rem,4vw,3rem) clamp(1rem,4vw,2rem) 5rem; }
.ltr { direction:ltr; unicode-bidi:isolate; text-align:left; }

header { border-bottom:2px solid var(--ink); padding-bottom:1.1rem; margin-bottom:2rem; }
h1 { font-size:clamp(1.6rem,4vw,2.2rem); margin:0 0 .4rem; }
.meta { color:var(--soft); font-size:.9rem; margin:0; }
.counts { display:flex; flex-wrap:wrap; gap:1.4rem; margin-top:1rem;
  font-size:.82rem; color:var(--faint); }
.counts b { display:block; font-size:1.5rem; color:var(--ink);
  font-variant-numeric:tabular-nums; line-height:1.2; }

h2 { font-size:1.2rem; margin:2.5rem 0 1rem; padding-bottom:.4rem;
  border-bottom:1px solid var(--rule); }

.job { background:var(--surface); border:1px solid var(--rule); border-radius:8px;
  padding:1.1rem 1.3rem 1.2rem; margin-bottom:1rem; box-shadow:var(--shadow); }
.job.top { border-right:4px solid var(--good); }
.head { display:flex; align-items:baseline; gap:.7rem; flex-wrap:wrap; }
.score { font-size:1.6rem; font-weight:700; font-variant-numeric:tabular-nums;
  color:var(--accent); line-height:1; }
.title { font-weight:600; font-size:1.05rem; }
.company { color:var(--soft); }
.badge { font-size:.7rem; font-weight:700; letter-spacing:.05em; padding:.15rem .55rem;
  border-radius:100px; text-transform:uppercase; }
.badge.apply { background:var(--good-soft); color:var(--good); }
.badge.review { background:var(--accent-soft); color:var(--accent); }
.badge.fresh { background:var(--warn-soft); color:var(--warn); }
.badge.dead { background:var(--warn); color:var(--paper); }
.dead-note { margin:.4rem 0 0; font-size:.82rem; color:var(--warn); }
.pick { width:1.1rem; height:1.1rem; accent-color:var(--accent); cursor:pointer; }
.bar-actions { position:sticky; top:0; z-index:9; background:var(--surface);
  border:1px solid var(--rule); border-radius:8px; padding:.7rem 1rem; margin-bottom:1.2rem;
  display:none; gap:.7rem; align-items:center; flex-wrap:wrap; box-shadow:var(--shadow); }
.bar-actions.on { display:flex; }
.bar-actions button { font:inherit; font-size:.87rem; font-weight:600; cursor:pointer;
  border:0; border-radius:5px; padding:.42rem .9rem; background:var(--accent-soft); color:var(--accent); }
.bar-actions button.primary { background:var(--accent); color:var(--paper); }
.bar-actions button.submit-batch { background:var(--good); color:var(--paper); }
.bar-actions button:disabled { opacity:.45; cursor:default; }
.bar-count { color:var(--soft); font-size:.87rem; margin-inline-end:auto; }
.log { font-family:"IBM Plex Mono",monospace; direction:ltr; unicode-bidi:isolate; text-align:left;
  font-size:.76rem; background:var(--code-bg); color:var(--code-fg); border-radius:6px;
  padding:.7rem .9rem; margin-top:.7rem; max-height:13rem; overflow:auto; white-space:pre-wrap; display:none; }
.log.on { display:block; }
nav.pages { display:flex; gap:.4rem; margin-bottom:1.4rem; }
nav.pages a { font-size:.88rem; font-weight:600; text-decoration:none; color:var(--soft);
  padding:.3rem .8rem; border-radius:5px; }
nav.pages a.here { background:var(--accent-soft); color:var(--accent); }
.badge.sent { background:var(--good); color:var(--paper); }
.badge.near { background:var(--good-soft); color:var(--good); }
.badge.siblings { background:var(--warn-soft); color:var(--warn); }
.coverage { margin-top:.8rem; display:flex; flex-wrap:wrap; gap:.3rem; align-items:center; }
.coverage h4 { width:100%; margin:0 0 .1rem; font-size:.72rem; letter-spacing:.1em;
  text-transform:uppercase; color:var(--faint); }
.chip { font-size:.78rem; font-weight:600; padding:.1rem .55rem; border-radius:100px; }
.chip.yes { background:var(--good-soft); color:var(--good); }
.chip.left { background:var(--warn-soft); color:var(--warn); }
.chip.no { background:var(--code-bg); color:var(--faint); text-decoration:line-through; }
.chip-note { font-size:.75rem; color:var(--warn); margin-inline-start:.3rem; }
.missing { margin:-.6rem 0 1rem; color:var(--warn); font-size:.85rem; }
.note { background:var(--accent-soft); border-radius:6px; padding:.7rem .9rem; margin-top:.8rem; }
.note h4 { margin:0 0 .3rem; font-size:.72rem; letter-spacing:.1em; text-transform:uppercase; color:var(--accent); }
.note p { margin:0; font-size:.9rem; line-height:1.6; }
details.detail { margin-top:.8rem; }
details.detail summary { cursor:pointer; font-size:.85rem; color:var(--soft); }
details.detail .cols { border-top:0; padding-top:.3rem; }
.badge.same-company { background:var(--accent-soft); color:var(--accent); }
button.mark-sent { font:inherit; font-size:.85rem; font-weight:600; cursor:pointer; border:1px solid var(--good);
  border-radius:5px; padding:.3rem .8rem; background:transparent; color:var(--good); margin-top:.8rem; }
form.update, form.manual, form.sent-form { display:flex; flex-wrap:wrap; gap:.5rem; align-items:end;
  margin-top:.7rem; padding-top:.7rem; border-top:1px solid var(--rule); }
form[hidden], .error[hidden] { display:none; }
.filter-bar { font-size:.9rem; color:var(--soft); margin:-.4rem 0 1rem; }
.filter-bar input { accent-color:var(--accent); margin-inline-end:.4rem; }
body.only-comeet .job[data-board="other"] { display:none; }
button.submit-now { font:inherit; font-size:.85rem; font-weight:700; cursor:pointer; border:0; border-radius:5px;
  padding:.32rem .9rem; background:var(--good); color:var(--paper); margin-top:.8rem; margin-inline-end:.4rem; }
button.submit-now:disabled { opacity:.45; cursor:default; }
button.dismiss { font:inherit; font-size:.85rem; cursor:pointer; border:0; background:transparent;
  color:var(--faint); padding:.3rem .6rem; margin-top:.8rem; }
button.dismiss:hover { color:var(--warn); }
details.removed-box { margin-top:2rem; }
details.removed-box summary { cursor:pointer; color:var(--soft); font-weight:600; }
button.restore { font:inherit; font-size:.8rem; cursor:pointer; border:0; border-radius:5px;
  padding:.2rem .6rem; background:var(--accent-soft); color:var(--accent); }
form label { display:flex; flex-direction:column; gap:.15rem; font-size:.75rem; color:var(--soft); }
form input, form select { font:inherit; font-size:.86rem; padding:.3rem .45rem; border:1px solid var(--rule);
  border-radius:5px; background:var(--paper); color:var(--ink); }
form input.wide { min-width:16rem; }
form button { font:inherit; font-size:.84rem; font-weight:600; cursor:pointer; border:0; border-radius:5px;
  padding:.38rem .9rem; background:var(--accent); color:var(--paper); }
form button.cancel { background:transparent; color:var(--soft); }
form button:disabled { opacity:.45; cursor:default; }
.error { color:var(--warn); font-size:.82rem; width:100%; margin:0; }
.doc-link { margin:.5rem 0 0; }
.doc-link a { display:inline-block; background:var(--accent-soft); color:var(--accent);
  padding:.35rem .8rem; border-radius:5px; font-size:.85rem; font-weight:600;
  text-decoration:none; }

.bars { display:flex; gap:1.2rem; margin:.8rem 0; font-size:.75rem; color:var(--faint); }
.bar b { display:block; color:var(--ink); font-size:1rem; font-variant-numeric:tabular-nums; }

ul { margin:.5rem 0; padding-right:1.1rem; }
li { margin-bottom:.35rem; }
.cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(15rem,1fr)); gap:1rem;
  margin-top:.9rem; padding-top:.9rem; border-top:1px solid var(--rule); }
.cols h4 { margin:0 0 .3rem; font-size:.72rem; letter-spacing:.1em; text-transform:uppercase;
  color:var(--faint); }
.cols li { font-size:.88rem; color:var(--soft); }
a.apply-link { display:inline-block; margin-top:.9rem; color:var(--accent);
  font-size:.85rem; word-break:break-all; }

table { width:100%; border-collapse:collapse; font-size:.88rem; }
td, th { padding:.45rem .6rem; border-bottom:1px solid var(--rule); text-align:right; }
th { font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; color:var(--faint); }
td.num { font-variant-numeric:tabular-nums; color:var(--soft); }
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--rule);
  color:var(--faint); font-size:.82rem; }
"""


def link_is_alive(url: str, timeout: float = 12.0) -> bool:
    """Check that a posting link actually opens.

    A company can leave a job live in its board feed while its own careers site
    returns nothing for it, which is how a dead link reached the report. Showing
    a link that goes nowhere costs more than the second it takes to check.
    """
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0 job-agent"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status < 400
        except urllib.error.HTTPError as error:
            if error.code in (403, 405):  # some sites refuse the probe but serve the page
                continue
            return False
        except Exception:
            continue
    return False


def check_links(jobs: list[dict], workers: int = 8) -> dict[str, bool]:
    """Test every link at once, because doing them in turn would make the report slow."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip((j["id"] for j in jobs), pool.map(lambda j: link_is_alive(j["url"]), jobs)))


def esc(text) -> str:
    return html.escape(str(text or ""))


def document_for(job: dict, folder: pathlib.Path) -> str | None:
    """Find the tailored document for a job, so the report links to it instead of hiding it."""
    import re as _re
    stem = _re.sub(r"[^a-z0-9]+", "-", f"{job['company']}-{job['title']}".lower()).strip("-")[:60]
    candidate = folder / f"{stem}.html"
    return f"{folder.name}/{candidate.name}" if candidate.exists() else None


CHANNEL_OPTIONS = [("site", "אתר החברה"), ("linkedin", "לינקדאין"), ("referral", "חבר מביא חבר"),
                   ("email", "מייל"), ("other", "אחר")]


def sent_files(job: dict) -> str:
    """Link the approved files, so the exact PDFs the submit button attaches can be opened first."""
    stem = store.stem(job["company"], job["title"])
    links = [f'<a href="ready/{stem}-{kind}.pdf" target="_blank" rel="noopener">{label}</a>'
             for kind, label in (("cv", "קורות החיים שיישלחו, PDF"), ("cover-letter", "המכתב שיישלח, PDF"))
             if (pathlib.Path("ready") / f"{stem}-{kind}.pdf").is_file()]
    return f'<p class="doc-link served-only">{" ".join(links)}</p>' if links else ""


def sent_form(job: dict) -> str:
    """The button that records an application, shown only when a server can receive it."""
    today = datetime.now().date().isoformat()
    channels = "".join(f'<option value="{k}">{v}</option>' for k, v in CHANNEL_OPTIONS)
    return f"""
  <div class="served-only">
    <button type="button" class="submit-now">הגש</button>
    <button type="button" class="mark-sent">הגשתי</button>
    <button type="button" class="dismiss">לא מעניין, הסר</button>
    <form class="sent-form" data-id="{esc(job['id'])}" hidden>
      <label>הוגש ב<input type="date" name="sent_at" value="{today}" max="{today}" required></label>
      <label>דרך<select name="channel">{channels}</select></label>
      <button type="submit">שמור ועבור למעקב</button>
      <button type="button" class="cancel">ביטול</button>
      <p class="error" hidden></p>
    </form>
  </div>"""


def cover_note_of(job: dict, folder: pathlib.Path) -> str:
    """Read the cover note out of the draft, so it can be read in the card instead of in a file."""
    path = folder / f"{store.stem(job['company'], job['title'])}.json"
    if not path.is_file():
        return ""
    try:
        return json.loads(path.read_text(encoding="utf-8"))["chosen"]["cover_note"].strip()
    except (ValueError, KeyError):
        return ""


def coverage_of(job: dict, folder: pathlib.Path, facts_text: str) -> dict[str, list[str]] | None:
    """Compare what the posting asks for with what the tailored CV says, when a draft exists."""
    path = folder / f"{store.stem(job['company'], job['title'])}.txt"
    if not path.is_file() or not facts_text:
        return None
    return keywords.coverage(job.get("description") or "", path.read_text(encoding="utf-8"), facts_text)


def coverage_block(result: dict[str, list[str]] | None) -> str:
    if not result:
        return ""
    chips = "".join(f'<span class="chip yes ltr">{esc(t)}</span>' for t in result["covered"])
    chips += "".join(f'<span class="chip left ltr">{esc(t)}</span>' for t in result["missed"])
    chips += "".join(f'<span class="chip no ltr">{esc(t)}</span>' for t in result["absent"])
    if not chips:
        return ""
    aside = ""
    if result["missed"]:
        aside = '<span class="chip-note">כתום: קיים אצלך בעובדות אבל לא נכנס לקו"ח</span>'
    return f'<div class="coverage"><h4>מה המשרה מבקשת</h4>{chips}{aside}</div>'


def job_block(job: dict, is_new: bool, top: bool, alive: bool = True, document: str | None = None,
              same_company: bool = False, siblings: int = 0, note: str = "",
              coverage: dict[str, list[str]] | None = None) -> str:
    years = filter_jobs.required_years(job.get("description") or "")
    demand = f"{years} שנות ניסיון" if years else "לא צוין ניסיון"
    badges = f'<span class="badge {esc(job["decision"])}">{esc(job["decision"])}</span>'
    if is_new:
        badges += '<span class="badge fresh">חדשה</span>'
    if not alive:
        badges += '<span class="badge dead">קישור שבור</span>'
    if same_company:
        badges += '<span class="badge same-company">כבר הגשת לחברה הזו</span>'
    # Recruiters see every application to their company on one profile, so two at once needs a choice.
    if siblings:
        badges += (f'<span class="badge siblings">עוד {siblings} מאותה חברה</span>'
                   if siblings > 1 else '<span class="badge siblings">עוד משרה מאותה חברה</span>')
    # A job in the home city is marked rather than ranked higher; the score stays about the job.
    home = settings.home_places()
    if home and any(place in (job.get("location") or "").lower() for place in home):
        badges += '<span class="badge near">קרוב לבית</span>'

    reasons = "".join(f'<li class="ltr">{esc(r)}</li>' for r in job["reasons"])
    matched = "".join(f'<li class="ltr">{esc(m)}</li>' for m in job["matched"]) or "<li>—</li>"
    gaps = "".join(f'<li class="ltr">{esc(g)}</li>' for g in job["gaps"]) or "<li>—</li>"

    return f"""
<article class="job{' top' if top else ''}" data-id="{esc(job['id'])}" data-doc="{'yes' if document else 'no'}" data-board="{'comeet' if 'comeet.com/jobs' in (job.get('url') or '') else 'other'}">
  <div class="head">
    <input type="checkbox" class="pick" value="{esc(job['id'])}" aria-label="select">
    <span class="score">{job['score']}</span>
    <span class="title ltr">{esc(job['title'])}</span>
    <span class="company ltr">{esc(job['company'])}</span>
    {badges}
  </div>
  <div class="bars">
    <span class="bar">סיכויים<b>{job['odds']}</b></span>
    <span class="bar">התאמה<b>{job['fit']}</b></span>
    <span class="bar">רצייה<b>{job['desirability']}</b></span>
    <span class="bar">ביטחון<b class="ltr">{esc(job['confidence'])}</b></span>
    <span class="bar">דרישה<b>{esc(demand)}</b></span>
    <span class="bar">מיקום<b class="ltr">{esc(job['location'])}</b></span>
  </div>
  <ul>{reasons}</ul>
  {coverage_block(coverage)}
  {f'<div class="note"><h4>מכתב הפנייה שנכתב למשרה</h4><p class="ltr">{esc(note)}</p></div>' if note else ''}
  <details class="detail"><summary>מתקיים ופערים</summary>
    <div class="cols">
      <div><h4>מתקיים</h4><ul>{matched}</ul></div>
      <div><h4>פערים</h4><ul>{gaps}</ul></div>
    </div>
  </details>
  <a class="apply-link ltr" href="{esc(job['url'])}">{esc(job['url'])}</a>
  {'<p class="dead-note">הקישור לא נפתח. חפש את המשרה בדף הקריירה של החברה.</p>' if not alive else ''}
  {f'<p class="doc-link"><a href="{document}">קורות חיים ומכתב מותאמים למשרה הזו</a></p>' if document else ''}
  {sent_files(job)}
  {sent_form(job)}
</article>"""


def render(jobs: list[dict], new_ids: set[str], counts: dict, rubric: str, alive: dict[str, bool],
           docs: dict[str, str], applied: dict[str, dict] | None = None,
           dismissed: dict[str, dict] | None = None) -> str:
    applied, dismissed = applied or {}, dismissed or {}
    # A job already sent or removed leaves the list, so the page keeps showing only what is still to do.
    done = [j for j in jobs if j["id"] in applied]
    removed = [j for j in jobs if j["id"] in dismissed and j["id"] not in applied]
    jobs = [j for j in jobs if j["id"] not in applied and j["id"] not in dismissed]
    companies = {a["company"].lower() for a in applied.values()}
    shortlist = [j for j in jobs if j["score"] >= counts["threshold"]]
    rest = [j for j in jobs if j["score"] < counts["threshold"]]

    # How many other jobs at the same company are on the list, so two are never sent by accident.
    per_company = collections.Counter(j["company"].lower() for j in shortlist)
    drafts = pathlib.Path("applications")
    facts_path = pathlib.Path("facts.md")
    facts_text = facts_path.read_text(encoding="utf-8") if facts_path.is_file() else ""
    covers = {j["id"]: coverage_of(j, drafts, facts_text) for j in shortlist} if drafts.is_dir() else {}
    blocks = "".join(
        job_block(j, j["id"] in new_ids, j["decision"] == "apply", alive.get(j["id"], True), docs.get(j["id"]),
                  j["company"].lower() in companies, per_company[j["company"].lower()] - 1,
                  cover_note_of(j, drafts) if drafts.is_dir() else "", covers.get(j["id"]))
        for j in shortlist
    )

    # What the postings keep asking for and facts.md cannot answer, which is a reading list, not a rejection.
    wanted = collections.Counter(t for c in covers.values() if c for t in c["absent"])
    missing_note = ""
    if wanted:
        top = ", ".join(f"{t} ({n})" for t, n in wanted.most_common(6))
        missing_note = (f'<p class="meta missing">חוזר בדרישות ואין לך: <span class="ltr">{esc(top)}</span></p>')
    if not shortlist:
        blocks = "<p>אף משרה לא עברה את הסף בהרצה הזו.</p>"

    done_rows = "".join(
        f'<tr><td class="num">{datetime.fromisoformat(applied[j["id"]]["sent_at"]):%d/%m}</td>'
        f'<td class="ltr">{esc(j["company"])}</td><td class="ltr">{esc(j["title"])}</td></tr>'
        for j in done
    )
    done_section = (
        '<h2>כבר הגשת</h2><p class="meta served-only">המצב של כל אחת מהן נמצא ב<a href="/tracking">מעקב ההגשות</a>.</p>'
        f'<table><thead><tr><th>הוגש</th><th>חברה</th><th>תפקיד</th></tr></thead><tbody>{done_rows}</tbody></table>'
    ) if done else ""

    removed_rows = "".join(
        f'<tr><td class="num">{j["score"]}</td><td class="ltr">{esc(j["company"])}</td>'
        f'<td class="ltr">{esc(j["title"])}</td>'
        f'<td><button type="button" class="restore" data-id="{esc(j["id"])}">החזר</button></td></tr>'
        for j in removed
    )
    removed_section = (
        f'<details class="served-only removed-box"><summary>הסרת {len(removed)} משרות</summary>'
        '<p class="meta">משרה שהוסרה לא תדורג ולא תוצג שוב, גם אם תתפרסם מחדש בקישור אחר.</p>'
        f'<table><tbody>{removed_rows}</tbody></table></details>'
    ) if removed else ""

    rows = "".join(
        f'<tr><td class="num">{j["score"]}</td><td class="ltr">{esc(j["company"])}</td>'
        f'<td class="ltr">{esc(j["title"])}</td></tr>'
        for j in rest
    )

    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>משרות, {datetime.now():%d/%m/%Y}</title><style>{CSS}</style></head>
<body><div class="doc">
<nav class="pages served-only"><a href="/" class="here">משרות</a><a href="/tracking">מעקב הגשות</a></nav>
<header>
  <h1>משרות שעברו סינון</h1>
  <p class="meta">{datetime.now():%d/%m/%Y %H:%M} · רובריקה <span class="ltr">{esc(rubric)}</span></p>
  <div class="counts">
    <span>נאספו<b>{counts['collected']}</b></span>
    <span>חדשות<b>{counts['new']}</b></span>
    <span>עברו סינון<b>{counts['kept']}</b></span>
    <span>מעל הסף<b>{len(shortlist)}</b></span>
  </div>
</header>

<div class="bar-actions" id="actions">
  <span class="bar-count" id="count"></span>
  <button id="btn-tailor">הכן קורות חיים מותאמים</button>
  <button id="btn-approve" class="primary">אשר והפק קבצים להגשה</button>
  <button id="btn-submit-batch" class="submit-batch">הגש את המסומנות</button>
</div>
<pre class="log" id="log"></pre>

<h2>שווה את תשומת ליבך</h2>
<p class="served-only filter-bar"><label><input type="checkbox" id="only-comeet">
  רק משרות שאפשר להגיש מכאן אוטומטית</label></p>
{missing_note}
{blocks}

{done_section}

{removed_section}

<h2>נבדקו ולא עברו את הסף</h2>
<table><thead><tr><th>ציון</th><th>חברה</th><th>תפקיד</th></tr></thead>
<tbody>{rows or '<tr><td colspan="3">אין</td></tr>'}</tbody></table>

<footer>
הציון הוא שבעים אחוז סיכויים ושלושים אחוז התאמה. רצייה מוצגת ואינה משוקללת.
ההגשה עצמה נעשית על ידך, דרך הקישור שבכל כרטיס.
</footer>
</div></body></html>"""


def write(db: str, profile_path: str, out: str, threshold: int, open_browser: bool = False, check: bool = True) -> int:
    """Build the page from the database. Called directly by run.py and by main below."""
    profile = pathlib.Path(profile_path)
    if not profile.exists():
        print(f"missing {profile}", file=sys.stderr)
        return 1

    connection = store.connect(db)
    rubric = store.rubric_id(profile.read_text(encoding="utf-8"))

    # Only jobs seen in the most recent collection, so a closed posting cannot linger.
    latest = connection.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
    open_ids = {row[0] for row in connection.execute("SELECT id FROM jobs WHERE last_seen = ?", (latest,))}
    new_ids = {row[0] for row in connection.execute("SELECT id FROM jobs WHERE first_seen = ?", (latest,))}

    jobs = [j for j in store.ranked(connection, rubric) if j["id"] in open_ids]
    # Apply today's hard rules to jobs scored under older ones, so a rule added later also clears
    # the page. Jobs already applied to stay, because the page lists them as sent.
    applied = store.applied_jobs(connection)
    jobs = [j for j in jobs if j["id"] in applied
            or filter_jobs.rejection_reason(j, filter_jobs.MAX_YEARS_REQUIRED) is None]
    if not jobs:
        print("nothing scored under the current rubric, run run.py first", file=sys.stderr)
        return 1

    run = connection.execute(
        "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    counts = {
        "collected": run["collected"] if run else len(jobs),
        "new": run["new_jobs"] if run else 0,
        "kept": run["kept"] if run else len(jobs),
        "threshold": threshold,
    }

    shortlist = [j for j in jobs if j["score"] >= threshold]
    alive: dict[str, bool] = {}
    if check and shortlist:
        alive = check_links(shortlist)
        broken = [j for j in shortlist if not alive[j["id"]]]
        if broken:
            print(f"{len(broken)} of {len(shortlist)} links do not open, flagged in the page:")
            for job in broken:
                print(f"  {job['company']} - {job['title'][:40]}")

    folder = pathlib.Path("applications")
    docs = {j["id"]: d for j in shortlist if (d := document_for(j, folder))} if folder.is_dir() else {}

    path = pathlib.Path(out)
    path.write_text(render(jobs, new_ids, counts, rubric, alive, docs, store.applied_jobs(connection),
                           store.dismissed_jobs(connection)),
                    encoding="utf-8")
    if docs:
        print(f"{len(docs)} of {len(shortlist)} shortlisted jobs have a tailored document linked")
    print(f"wrote {path}, {len(shortlist)} jobs above {threshold} out of {len(jobs)} scored")

    if open_browser:
        webbrowser.open(path.resolve().as_uri())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the run report as one page.")
    parser.add_argument("--db", default=store.DB_PATH)
    parser.add_argument("--profile", default="profile.md")
    parser.add_argument("--out", default="report.html")
    parser.add_argument("--threshold", type=int, default=50)
    parser.add_argument("--open", action="store_true", help="open the page in a browser when it is written")
    parser.add_argument("--no-check", action="store_true", help="skip testing that each link opens")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    return write(args.db, args.profile, args.out, args.threshold, args.open, not args.no_check)


if __name__ == "__main__":
    raise SystemExit(main())
