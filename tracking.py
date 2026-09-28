"""Track what was sent and what came back, as a second page next to the report.

The report answers which jobs are worth applying to. This page answers what
happened after you did. Both read the same database, so marking a job as sent on
the report moves it here without anything to copy by hand.

Every change is an event appended to a log, and the stage shown is whatever the
latest event says. A wrong update is fixed by adding the right one, not by
editing history.

Run directly with --json to get the same state as structured data, which is how
another script or a Claude session reads it without opening the page.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
from datetime import date

import report
import store
from report import esc

KIND_LABELS = {
    "sent": "נשלח", "confirmation": "אושרה קבלה", "screening": "שיחת סינון",
    "assessment": "מטלה", "interview": "ראיון", "offer": "הצעה",
    "rejection": "נדחה", "withdrawn": "משכתי", "closed": "המשרה נסגרה",
    "silence": "אין מענה", "note": "הערה",
}
CHANNEL_LABELS = {"site": "אתר החברה", "linkedin": "לינקדאין", "referral": "חבר מביא חבר",
                  "email": "מייל", "other": "אחר"}

ACTIVE = {"screening", "assessment", "interview", "offer"}
WAITING = {"sent", "confirmation"}
NUDGE_DAYS = 14

CSS = """
.app { background:var(--surface); border:1px solid var(--rule); border-radius:8px;
  padding:.9rem 1.1rem; margin-bottom:.7rem; }
.app .head { display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap; }
.stage { font-size:.75rem; font-weight:700; padding:.12rem .6rem; border-radius:100px; white-space:nowrap; }
.stage.waiting { background:var(--accent-soft); color:var(--accent); }
.stage.active { background:var(--good-soft); color:var(--good); }
.stage.ended { background:var(--paper); color:var(--faint); border:1px solid var(--rule); }
.sub { color:var(--soft); font-size:.86rem; margin:.3rem 0 0; }
.sub .quiet { color:var(--warn); font-weight:600; }
.tools { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; margin-top:.6rem; }
.tools a, .tools button, .tools summary { font:inherit; font-size:.82rem; font-weight:600; cursor:pointer;
  border:0; border-radius:5px; padding:.3rem .75rem; background:var(--accent-soft); color:var(--accent);
  text-decoration:none; list-style:none; }
.tools button.quiet-btn { background:transparent; color:var(--faint); margin-inline-start:auto; }
details.answers { margin-top:.6rem; font-size:.86rem; }
details.answers summary { cursor:pointer; color:var(--accent); font-weight:600; }
details.answers dl { margin:.5rem 0 0; display:grid; gap:.15rem .8rem; }
details.answers dt { font-weight:600; color:var(--ink); margin-top:.4rem; }
details.answers dd { margin:0; color:var(--soft); }
details.answers .source { font-size:.72rem; color:var(--faint); unicode-bidi:isolate; }
ol.log-events { margin:.6rem 0 0; padding-right:1.1rem; font-size:.84rem; color:var(--soft); }
ol.log-events li { margin-bottom:.2rem; }
.attention { border-right:4px solid var(--warn); }
.empty { color:var(--soft); }
details.manual-box { margin-top:2.5rem; }
details.manual-box summary { cursor:pointer; font-weight:600; color:var(--accent); }
"""


def freeze(job: dict, sent_at: str, ready: pathlib.Path, root: pathlib.Path,
           answers: list[dict] | None = None) -> str | None:
    """Copy what was sent and the posting text, because drafts get rewritten and postings get removed."""
    name = store.stem(job["company"], job["title"])
    folder = root / f"{sent_at}-{name}"
    documents = [ready / f"{name}-{kind}.{ext}" for kind in ("cv", "cover-letter") for ext in ("docx", "pdf")]
    if not any(d.is_file() for d in documents) and not job.get("description") and not answers:
        return None

    folder.mkdir(parents=True, exist_ok=True)
    for document in documents:
        if document.is_file():
            shutil.copy2(document, folder / document.name)
    if job.get("description"):
        (folder / "posting.txt").write_text(
            f"{job['company']}\n{job['title']}\n{job['url']}\n\n{job['description']}", encoding="utf-8")
    if answers:
        (folder / "form-answers.txt").write_text(
            "\n\n".join(f"Q: {a['question']}\nA: {a['answer']}" for a in answers), encoding="utf-8")
    return folder.as_posix()


def handle(action: str, payload: dict, options) -> tuple[int, dict]:
    """Apply one change from the page and say what happened, in words the page can show."""
    connection = store.connect(options.db)
    try:
        if action == "sent":
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (payload.get("job_id"),)).fetchone()
            if job is None:
                return 404, {"error": "המשרה לא נמצאה במאגר"}
            job = dict(job)
            sent_at = store.check_date(payload.get("sent_at"))
            if payload.get("channel") not in store.CHANNELS:
                return 400, {"error": "ערוץ הגשה לא מוכר"}
            if connection.execute("SELECT 1 FROM applications WHERE job_id = ?", (job["id"],)).fetchone():
                return 409, {"error": "המשרה הזו כבר מסומנת כהוגשה"}
            frozen = freeze(job, sent_at, pathlib.Path(options.ready), pathlib.Path(options.sent),
                            store.form_answers(connection, job["id"]))
            store.add_application(connection, job["company"], job["title"], job["url"],
                                  payload.get("channel"), sent_at, job["id"], frozen)
        elif action == "application":
            url = (payload.get("url") or "").strip() or None
            known = connection.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone() if url else None
            store.add_application(connection, payload.get("company"), payload.get("title"), url,
                                  payload.get("channel"), payload.get("sent_at"), known["id"] if known else None)
        elif action == "event":
            if payload.get("kind") == "sent":
                return 400, {"error": "ההגשה כבר רשומה"}
            store.add_event(connection, int(payload.get("application_id", 0)), payload.get("kind"),
                            payload.get("at"), payload.get("note", ""))
        elif action == "remove":
            store.remove_application(connection, int(payload.get("application_id", 0)))
        elif action == "dismiss":
            if payload.get("job_id") in store.applied_jobs(connection):
                return 409, {"error": "כבר הגשת למשרה הזו, היא נמצאת במעקב"}
            store.dismiss_job(connection, payload.get("job_id"))
        elif action == "restore":
            store.restore_job(connection, payload.get("job_id"))
        elif action == "mail_confirm":
            import mail_sync
            mail_sync.confirm(connection, payload.get("message_id"), payload.get("job_id") or None)
        elif action == "mail_ignore":
            import mail_sync
            mail_sync.ignore(connection, payload.get("message_id"))
        else:
            return 404, {"error": "פעולה לא מוכרת"}
        return 200, {"ok": True}
    except LookupError as error:
        return 409, {"error": str(error)}
    except (ValueError, TypeError) as error:
        return 400, {"error": str(error)}
    finally:
        connection.close()


def stage_class(stage: str) -> str:
    return "active" if stage in ACTIVE else "waiting" if stage in WAITING else "ended"


def needs_attention(app: dict) -> str | None:
    """Say why an application deserves a look now, or nothing when it does not."""
    if app["stage"] in ACTIVE:
        return "בתהליך, לא לשכוח להתכונן ולענות"
    if app["stage"] in WAITING and app["days_quiet"] >= NUDGE_DAYS:
        return f"{app['days_quiet']} ימים בלי מענה, אפשר לפנות שוב"
    return None


def options_html(pairs, selected: str | None = None) -> str:
    return "".join(f'<option value="{k}"{" selected" if k == selected else ""}>{v}</option>' for k, v in pairs)


def app_block(app: dict, today: str, attention: str | None) -> str:
    stage = app["stage"]
    sent = date.fromisoformat(app["sent_at"])
    days = (date.today() - sent).days
    since = "היום" if days == 0 else "אתמול" if days == 1 else f"לפני {days} ימים"
    quiet = f' · <span class="quiet">{esc(attention)}</span>' if attention else ""

    events = "".join(
        f'<li>{date.fromisoformat(e["at"]):%d/%m} · {KIND_LABELS.get(e["kind"], e["kind"])}'
        f'{" · זוהה מהמייל" if e["source"] == "mail" else ""}'
        # The note is often English from the mail step, so it gets its own direction or the line scrambles.
        f'{" · <span class=ltr>" + esc(e["note"]) + "</span>" if e["note"] else ""}</li>'
        for e in reversed(app["events"])
    )

    links = ""
    if app["url"]:
        links += f'<a href="{esc(app["url"])}" target="_blank" rel="noopener">המשרה</a>'
    if app["frozen_dir"]:
        folder = pathlib.Path(app["frozen_dir"])
        if folder.is_dir():
            labels = {"posting.txt": "תיאור המשרה כפי שנשמר", "form-answers.txt": "התשובות בטופס, כקובץ"}
            for file in sorted(folder.iterdir()):
                kind = "PDF" if file.suffix == ".pdf" else "Word"
                label = labels.get(file.name, f"קורות חיים, {kind}" if "-cv." in file.name
                                   else f"מכתב, {kind}" if "cover-letter" in file.name else file.name)
                links += f'<a href="/{esc(file.as_posix())}">{label}</a>'

    sources = {"standing": "קבועה", "drafted": "טיוטה שאישרת", "you": "הקלדת"}
    answered = "".join(
        f'<dt class="ltr">{esc(a["question"])}</dt><dd class="ltr">{esc(a["answer"])}'
        f' <span class="source">{sources.get(a["source"], a["source"])}</span></dd>'
        for a in app.get("answers", [])
    )
    answers_box = (f'<details class="answers"><summary>מה עניתי בטופס ({len(app["answers"])})</summary>'
                   f'<dl>{answered}</dl></details>') if answered else ""

    kinds = [(k, v) for k, v in KIND_LABELS.items() if k != "sent"]
    return f"""
<article class="app{' attention' if attention else ''}" data-app="{app['id']}">
  <div class="head">
    <span class="stage {stage_class(stage)}">{KIND_LABELS.get(stage, stage)}</span>
    <span class="title ltr">{esc(app['title'])}</span>
    <span class="company ltr">{esc(app['company'])}</span>
  </div>
  <p class="sub">הוגש {sent:%d/%m/%Y}, {since}, דרך {CHANNEL_LABELS.get(app['channel'], app['channel'])}{quiet}</p>
  <div class="tools">
    <button type="button" class="open-update">עדכון</button>
    {links}
    <details><summary>היסטוריה</summary><ol class="log-events">{events}</ol></details>
    <button type="button" class="quiet-btn remove">הסר, סומן בטעות</button>
  </div>
  {answers_box}
  <form class="update" hidden>
    <label>מה קרה<select name="kind">{options_html(kinds)}</select></label>
    <label>מתי<input type="date" name="at" value="{today}" min="{app['sent_at']}" max="{today}" required></label>
    <label>הערה<input type="text" name="note" maxlength="500" class="wide" placeholder="לא חובה"></label>
    <button type="submit">שמור</button>
    <p class="error" hidden></p>
  </form>
</article>"""


def pending_block(mail: dict, candidates: list[dict]) -> str:
    """One message the mail step could not settle alone, with what it read and the jobs it might be."""
    options = "".join(
        f'<option value="{esc(c["id"])}">{esc(c["company"])} · {esc(c["title"])}</option>' for c in candidates
    )
    choose = (f'<label>המשרה<select name="job_id"><option value="">בלי קישור למשרה</option>{options}</select></label>'
              if candidates else "")
    reading = f"{KIND_LABELS.get(mail['kind'], mail['kind'] or '')} · {mail['company'] or 'חברה לא ידועה'}"
    return f"""
<article class="app attention" data-message="{esc(mail['message_id'])}">
  <div class="head">
    <span class="stage waiting">{esc(reading)}</span>
    <span class="title ltr">{esc(mail['title'] or '')}</span>
  </div>
  <p class="sub">{date.fromisoformat(mail['received_at']):%d/%m/%Y} · <span class="ltr">{esc(mail['subject'])}</span></p>
  <p class="sub ltr">{esc(mail['sender'])}</p>
  <form class="mail-confirm">
    {choose}
    <button type="submit">נכון, רשום</button>
    <button type="button" class="cancel mail-ignore">לא קשור להגשה</button>
    <p class="error" hidden></p>
  </form>
</article>"""


def pending_mail(connection) -> str:
    import json as _json
    import mail_sync
    rows = [dict(r) for r in connection.execute(
        "SELECT * FROM mail_messages WHERE status = 'pending' ORDER BY received_at DESC")]
    if not rows:
        return ""
    jobs = [dict(r) for r in connection.execute("SELECT id, company, title FROM jobs")]
    blocks = []
    for mail in rows:
        known = (_json.loads(mail["detail"] or "{}").get("candidates") or [])
        candidates = [j for j in jobs if j["id"] in known] or \
                     [j for j in jobs if mail["company"] and mail_sync.same_company(j["company"], mail["company"])][:8]
        blocks.append(pending_block(mail, candidates))
    return ("<h2>מהמייל, מחכה לאישור שלך</h2><p class=\"meta\">הודעות שנראות קשורות להגשה, "
            "אבל לא היה ברור לאיזו. שום דבר לא נרשם עד שתאשר.</p>" + "".join(blocks))


def render(apps: list[dict], pending: str = "") -> str:
    today = date.today().isoformat()
    attention = {a["id"]: needs_attention(a) for a in apps}
    flagged = [a for a in apps if attention[a["id"]]]
    active = [a for a in apps if a["stage"] in ACTIVE]
    waiting = [a for a in apps if a["stage"] in WAITING]
    ended = [a for a in apps if a["stage"] not in ACTIVE | WAITING]

    def section(title: str, items: list[dict], empty: str) -> str:
        body = "".join(app_block(a, today, attention[a["id"]]) for a in items)
        return f"<h2>{title}</h2>" + (body or f'<p class="empty">{empty}</p>')

    counts = {
        "הגשות": len(apps),
        "בתהליך": len(active),
        "ממתינות": len(waiting),
        "נדחו": sum(a["stage"] == "rejection" for a in apps),
        f"שקטות {NUDGE_DAYS}+ ימים": sum(a["stage"] in WAITING and a["days_quiet"] >= NUDGE_DAYS for a in apps),
    }
    counts_html = "".join(f"<span>{k}<b>{v}</b></span>" for k, v in counts.items())
    flagged_note = ("<p class=\"meta\">הגשה אחת מסומנת כשווה תשומת לב.</p>" if len(flagged) == 1 else
                    f"<p class=\"meta\">{len(flagged)} הגשות מסומנות כשוות תשומת לב.</p>" if flagged else "")

    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>מעקב הגשות</title><style>{report.CSS}{CSS}</style></head>
<body><div class="doc">
<nav class="pages"><a href="/">משרות</a><a href="/tracking" class="here">מעקב הגשות</a></nav>
<header>
  <h1>מעקב הגשות</h1>
  {flagged_note}
  <div class="counts">{counts_html}</div>
</header>

{pending}

{section("בתהליך", active, "אין כרגע הגשות בשלב סינון, מטלה או ראיון.")}
{section("ממתינות לתשובה", waiting, "אין הגשות שממתינות.")}
{section("הסתיימו", ended, "עדיין אין.")}

<details class="manual-box">
  <summary>הוספת הגשה שלא מופיעה בדוח המשרות</summary>
  <form class="manual">
    <label>חברה<input type="text" name="company" required></label>
    <label>תפקיד<input type="text" name="title" required class="wide"></label>
    <label>קישור<input type="url" name="url" class="wide" placeholder="לא חובה"></label>
    <label>הוגש ב<input type="date" name="sent_at" value="{today}" max="{today}" required></label>
    <label>דרך<select name="channel">{options_html(CHANNEL_LABELS.items())}</select></label>
    <button type="submit">הוסף</button>
    <p class="error" hidden></p>
  </form>
</details>

<footer>הסטטוס של כל הגשה הוא העדכון האחרון שנרשם לה. עדכון שגוי מתקנים בעדכון נוסף.</footer>
</div><script src="/tracking.js"></script></body></html>"""


def page(db: str) -> str:
    connection = store.connect(db)
    try:
        apps = store.applications(connection)
        for app in apps:
            app["answers"] = store.form_answers(connection, app["job_id"]) if app["job_id"] else []
        return render(apps, pending_mail(connection))
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the state of every application.")
    parser.add_argument("--db", default=store.DB_PATH)
    parser.add_argument("--json", action="store_true", help="structured output, for scripts and agents")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    connection = store.connect(args.db)
    apps = store.applications(connection)
    for app in apps:
        app["attention"] = needs_attention(app)
        app["answers"] = store.form_answers(connection, app["job_id"]) if app["job_id"] else []

    if args.json:
        print(json.dumps(apps, ensure_ascii=False, indent=2))
        return 0
    if not apps:
        print("no applications recorded yet")
    for app in apps:
        flag = f"  <- {app['attention']}" if app["attention"] else ""
        print(f"{app['sent_at']}  {app['stage']:<13} {app['company'][:16]:<17} {app['title'][:40]}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
