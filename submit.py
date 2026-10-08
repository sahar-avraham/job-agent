"""Submit one approved application, after the candidate presses the button for it.

Nothing here runs on its own. The report's submit button calls it for one job at a
time, and only when that job has Word files in ready/, which exist only after the
approval step re-audited the documents and the candidate read them.

Comeet publishes an email address for every position, and a message to it with the
CV attached is an application. That is the only channel this file sends through.
Greenhouse and Ashby accept applications only through their web forms, which are
handled separately so the form is checked before it is sent.

Before sending it checks, in order: the job is on Comeet, it is still open, the
approved files exist, and nothing has been sent to this job or its repost before.
After sending it records the application, keeps a copy of exactly what went out,
and never sends the same job twice.
"""

from __future__ import annotations

import use_venv  # noqa: F401, must come first: relaunches under .venv when started with another Python

import argparse
import json
import mimetypes
import pathlib
import re
import smtplib
import ssl
import sys
from datetime import date
from email.message import EmailMessage
from email.utils import make_msgid

import fetch_jobs
import mail_sync
import settings
import store
import tracking

SMTP_HOST = "smtp.gmail.com"
COMEET_JOB = re.compile(r"comeet\.com/jobs/[^/]+/([0-9A-F]{2}\.[0-9A-F]{3})/[^/]*/([0-9A-F]{2}\.[0-9A-F]{3})", re.I)


class SubmitError(RuntimeError):
    """A reason not to send, worded for the page, since that is where it is read."""


def comeet_address(job: dict) -> str:
    """Find the position's application address in the live feed, which also proves the job is still open."""
    match = COMEET_JOB.search(job["url"] or "")
    if not match:
        raise SubmitError("הגשה אוטומטית זמינה כרגע רק למשרות מ-Comeet")
    company_uid, position_uid = match.group(1).upper(), match.group(2).upper()
    token = next((t.split(":", 1)[1] for _, board, t in fetch_jobs.COMPANIES
                  if board == "comeet" and t.split(":", 1)[0].upper() == company_uid), None)
    if token is None:
        raise SubmitError("החברה לא נמצאת ברשימת החברות")
    feed = fetch_jobs.fetch_json(fetch_jobs.COMEET_URL.format(uid=company_uid, token=token, details="false"))
    position = next((p for p in feed if (p.get("uid") or "").upper() == position_uid), None)
    if position is None:
        raise SubmitError("המשרה כבר לא מופיעה אצל החברה, כנראה נסגרה")
    address = position.get("email") or ""
    if "@" not in address:
        raise SubmitError("החברה לא פרסמה כתובת הגשה למשרה הזו")
    return address


def approved_files(job: dict, ready: pathlib.Path, drafts: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str]:
    """Return the approved CV, cover letter and cover note, or say which step is missing."""
    stem = store.job_stem(job)
    # The PDF when there is one, because it looks the same in every viewer; the Word file otherwise.
    cv, cover = (next((p for p in (ready / f"{stem}-{kind}.pdf", ready / f"{stem}-{kind}.docx") if p.is_file()),
                      ready / f"{stem}-{kind}.docx") for kind in ("cv", "cover-letter"))
    selection = drafts / f"{stem}.json"
    if not cv.is_file() or not selection.is_file():
        raise SubmitError("אין עדיין קורות חיים מאושרים למשרה הזו. הכן אותם ואשר קודם")
    note = json.loads(selection.read_text(encoding="utf-8"))["chosen"]["cover_note"].strip()
    return cv, cover, note


def subject_for(job: dict) -> str:
    return f"Application: {job['title']} - {settings.candidate_name()}"


def compose(job: dict, sender: str, recipient: str, note: str, files: list[pathlib.Path]) -> EmailMessage:
    name = settings.candidate_name()
    message = EmailMessage()
    message["From"] = f"{name} <{sender}>"
    message["To"] = recipient
    message["Subject"] = subject_for(job)
    message["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    # The address is an application channel, not a person's inbox: it turns the mail into a
    # candidate the way a web form does, and a form takes the cover letter as its own file. So
    # the letter goes only in the attachment and the body stays short, and a recruiter never
    # reads the same letter twice however the system shows the mail.
    signature = "\n".join(p for p in (name, settings.get("CANDIDATE_PHONE")) if p)
    # The subject carries the posting's title, so the body does not repeat it in a sentence.
    message.set_content("Hello,\n\nI'm applying for this position. "
                        "My CV and cover letter are attached.\n\nThank you for your time,\n" + signature)
    for file in files:
        kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        main, sub = kind.split("/", 1)
        # A readable name for the recruiter, instead of the internal file stem.
        label = "CV" if "-cv." in file.name else "Cover Letter"
        message.add_attachment(file.read_bytes(), maintype=main, subtype=sub,
                               filename=f"{name} - {label}{file.suffix}")
    return message


def preview(job_id: str, options) -> dict:
    """Say what would be sent, without sending, so the page can show it before the click counts."""
    connection = store.connect(options.db)
    try:
        job = check(connection, job_id)
        # The channel first, so a job that cannot be sent this way says so before asking for documents.
        recipient = comeet_address(job)
        cv, cover, note = approved_files(job, pathlib.Path(options.ready), pathlib.Path(options.drafts))
        return {"to": recipient, "subject": subject_for(job),
                "files": ["קורות חיים" if f == cv else "מכתב פנייה" for f in (cv, cover) if f.is_file()],
                "company": job["company"]}
    finally:
        connection.close()


def check(connection, job_id: str) -> dict:
    row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise SubmitError("המשרה לא נמצאה במאגר")
    if job_id in store.applied_jobs(connection):
        raise SubmitError("כבר הגשת למשרה הזו")
    if job_id in store.dismissed_jobs(connection):
        raise SubmitError("המשרה הזו מסומנת כלא מעניינת")
    return dict(row)


def submit(job_id: str, options) -> int:
    """Send one application and record it. Returns the application id."""
    connection = store.connect(options.db)
    try:
        job = check(connection, job_id)
        recipient = comeet_address(job)
        cv, cover, note = approved_files(job, pathlib.Path(options.ready), pathlib.Path(options.drafts))
        found = mail_sync.credentials()
        if found is None:
            raise SubmitError("אין פרטי ג'ימייל בקובץ ההגדרות")
        sender, password = found

        files = [f for f in (cv, cover) if f.is_file()]
        message = compose(job, sender, recipient, note, files)
        print(f"sending to {recipient}, {len(files)} files attached")
        with smtplib.SMTP_SSL(SMTP_HOST, 465, context=ssl.create_default_context(), timeout=60) as smtp:
            smtp.login(sender, password)
            smtp.send_message(message)
        print("sent")

        # Record only after the server accepted it, so a failure never looks like an application.
        today = date.today().isoformat()
        answers = [{"question": "Email to " + recipient, "answer": message.get_body(("plain",)).get_content()}]
        frozen = tracking.freeze(job, today, pathlib.Path(options.ready), pathlib.Path(options.sent), answers)
        application = store.add_application(connection, job["company"], job["title"], job["url"],
                                            "email", today, job["id"], frozen)
        connection.execute("UPDATE events SET source = 'submit', evidence = ? WHERE application_id = ? AND kind = 'sent'",
                           (message["Message-ID"], application))
        connection.commit()
        store.save_form_answers(connection, job["id"], [("Application email body", answers[0]["answer"], "drafted")])
        print(f"recorded as application {application}")
        return application
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit one approved application. The report's button calls this.")
    parser.add_argument("job_id")
    parser.add_argument("--db", default=store.DB_PATH)
    parser.add_argument("--ready", default="ready")
    parser.add_argument("--drafts", default="applications")
    parser.add_argument("--sent", default="sent")
    parser.add_argument("--preview", action="store_true", help="show what would be sent, send nothing")
    args = parser.parse_args()
    fetch_jobs.use_utf8_output()
    try:
        if args.preview:
            print(json.dumps(preview(args.job_id, args), ensure_ascii=False, indent=2))
        else:
            submit(args.job_id, args)
    except SubmitError as error:
        print(f"not sent: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
