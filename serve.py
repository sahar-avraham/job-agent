"""Serve the report locally so its buttons actually do something.

A report opened from disk can show checkboxes but cannot act on them, because a
page has no way to run Python. This puts a small server behind the same page, so
selecting jobs and pressing a button runs the real steps.

It binds to the loopback address only. Nothing here is reachable from the network,
and that is deliberate: the buttons write files and spend model calls.
"""

from __future__ import annotations

import use_venv  # noqa: F401, must come first: relaunches under .venv when started with another Python

import argparse
import io
import json
import pathlib
import re
import sys
import threading
import urllib.parse
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import report
import store
import tailor
import sources
import tracking

STATE = {"running": False, "lines": [], "reload": False}
TRACKING_ACTIONS = {"/api/sent": "sent", "/api/event": "event",
                    "/api/application": "application", "/api/remove": "remove",
                    "/api/dismiss": "dismiss", "/api/restore": "restore",
                    "/api/mail-confirm": "mail_confirm", "/api/mail-ignore": "mail_ignore"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}
LOCK = threading.Lock()


def note(line: str) -> None:
    with LOCK:
        STATE["lines"].append(line)
        del STATE["lines"][:-400]


class Runner(io.TextIOBase):
    """Feed whatever a step prints into the page's log, line by line."""

    def write(self, text: str) -> int:
        for line in text.splitlines():
            if line.strip():
                note(line.rstrip())
        return len(text)


def work(action: str, ids: list[str], options) -> None:
    """Run one step in the background, so the page stays responsive while it goes."""
    with LOCK:
        STATE.update(running=True, lines=[], reload=False)

    runner = Runner()
    try:
        with redirect_stdout(runner), redirect_stderr(runner):
            if action == "tailor":
                run_tailor(ids, options)
            elif action == "submit":
                run_submit(ids, options)
            else:
                run_approve(ids, options)
    except Exception as error:
        note(f"failed: {error}")
    finally:
        with LOCK:
            STATE.update(running=False, reload=True)
        note("done")


BATCH_LIMIT = 10
BATCH_PAUSE_SECONDS = 20


def run_submit(ids: list[str], options) -> None:
    """Send the confirmed applications one after another, skipping any that fail a check.

    Capped and spaced out, because a burst of mail from a personal address is what
    spam filters notice, and a mistake repeated across a batch costs the most.
    """
    import time
    import submit
    if not ids or len(ids) > BATCH_LIMIT:
        note(f"between 1 and {BATCH_LIMIT} jobs per batch, nothing sent")
        return
    sent = 0
    for index, identifier in enumerate(ids, start=1):
        note(f"[{index}/{len(ids)}]")
        try:
            submit.submit(identifier, options)
            sent += 1
        except submit.SubmitError as error:
            note(f"    not sent: {error}")
        except Exception as error:
            note(f"    failed: {str(error)[:160]}")
        if index < len(ids):
            time.sleep(BATCH_PAUSE_SECONDS)
    note(f"{sent} of {len(ids)} sent")


def run_tailor(ids: list[str], options) -> None:
    """Write a draft for each selected job, whatever its decision was."""
    facts = tailor.load_facts(pathlib.Path(options.facts))
    connection = store.connect(options.db)
    rubric = store.rubric_id(pathlib.Path(options.profile).read_text(encoding="utf-8"))
    jobs = {j["id"]: j for j in store.ranked(connection, rubric)}
    folder = pathlib.Path(options.drafts)
    folder.mkdir(exist_ok=True)

    for index, identifier in enumerate(ids, start=1):
        job = jobs.get(identifier)
        if job is None:
            note(f"[{index}/{len(ids)}] {identifier} is not in the database")
            continue
        note(f"[{index}/{len(ids)}] {job['company']} - {job['title'][:44]}")
        try:
            chosen, audit, document = tailor.tailor(job, facts, options.model)
        except Exception as error:
            note(f"    failed: {str(error)[:160]}")
            continue

        stem = store.job_stem(job)
        (folder / f"{stem}.html").write_text(tailor.render_html(chosen, facts, job), encoding="utf-8")
        (folder / f"{stem}.txt").write_text(document, encoding="utf-8")
        (folder / f"{stem}.json").write_text(
            json.dumps({"chosen": chosen.model_dump(),
                        "job": {k: job.get(k) for k in ("company", "title", "url", "score")}},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if audit.unsupported or audit.exaggerations:
            note("    the audit flagged something, read it before approving:")
            for item in audit.unsupported + audit.exaggerations:
                note(f"      {item}")
        else:
            note("    audit clean")


def run_approve(ids: list[str], options) -> None:
    """Turn selected drafts into Word files, re-checking each one first."""
    import approve
    import render_docx

    facts = tailor.load_facts(pathlib.Path(options.facts))
    drafts = pathlib.Path(options.drafts)
    ready = pathlib.Path(options.ready)
    connection = store.connect(options.db)
    rubric = store.rubric_id(pathlib.Path(options.profile).read_text(encoding="utf-8"))
    jobs = {j["id"]: j for j in store.ranked(connection, rubric)}

    for index, identifier in enumerate(ids, start=1):
        job = jobs.get(identifier, {})
        stem = store.job_stem(job, connection) if job else ""
        selection_file = drafts / f"{stem}.json"
        note(f"[{index}/{len(ids)}] {job.get('company','')} - {str(job.get('title',''))[:40]}")
        if not selection_file.exists():
            note("    no draft yet, press the other button for this one first")
            continue

        selection = json.loads(selection_file.read_text(encoding="utf-8"))
        clean = approve.reaudit(selection, facts, drafts, stem, options.model)
        if not clean:
            note("    not written, the audit flagged something above")
            continue
        for written in render_docx.render_both(selection["chosen"], facts, selection["job"], ready, stem):
            note(f"    wrote {written}")


MAIL_FRESH_MINUTES = 60
MAIL = {"running": False, "result": None}
MAIL_LOCK = threading.Lock()


def mail_check(options, force: bool) -> dict:
    """Read the mailbox in the background when the tracking page opens, unless it was read in the last hour.

    The tracking page is the only place the replies matter, so the mailbox is read when someone
    looks at it rather than on a timer while no one does. The button on the page forces a read.
    """
    from datetime import datetime, timedelta, timezone

    with MAIL_LOCK:
        if MAIL["running"]:
            return {"running": True}
        connection = store.connect(options.db)
        last = store.get_meta(connection, "mail_checked_at")
        connection.close()
        fresh = last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(minutes=MAIL_FRESH_MINUTES)
        if fresh and not force:
            return {"running": False, "fresh": True}
        MAIL.update(running=True, result=None)
    threading.Thread(target=read_mail, args=(options,), daemon=True).start()
    return {"running": True}


def read_mail(options) -> None:
    import mail_sync

    connection = store.connect(options.db)
    try:
        result = mail_sync.sync(connection, options.model)
    except Exception as error:
        result = {"error": str(error)[:160]}
    finally:
        connection.close()
    with MAIL_LOCK:
        MAIL.update(running=False, result=result)


UPLOAD_FOLDER = pathlib.Path.home() / "Downloads" / "job-agent-upload"


def upload_folder(job_id: str, options) -> dict:
    """Put one job's approved files in a folder of their own and open it, for a form filled by hand.

    The files get the names a recruiter should see. The folder holds only the job prepared last,
    so a file from another job cannot be picked up by mistake, and a file picker opens there again
    next time because it remembers the last folder used.
    """
    import os
    import shutil
    import forms
    import settings

    connection = store.connect(options.db)
    row = connection.execute("SELECT id, company, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        connection.close()
        return {"error": "המשרה לא נמצאה במאגר"}
    stem = store.job_stem(dict(row), connection)
    connection.close()
    ready = pathlib.Path(options.ready)
    name = settings.candidate_name()
    files = {f"{name} - CV.pdf": ready / f"{stem}-cv.pdf",
             f"{name} - Cover Letter.pdf": ready / f"{stem}-cover-letter.pdf",
             f"{name} - Transcript.pdf": forms.TRANSCRIPT}
    if not files[f"{name} - CV.pdf"].is_file():
        return {"error": "אין קבצים מאושרים למשרה הזו, אשר והפק קודם"}
    if UPLOAD_FOLDER.exists():
        shutil.rmtree(UPLOAD_FOLDER)
    target = UPLOAD_FOLDER / re.sub(r'[<>:"/\\|?*]+', " ", f"{row['company']} - {row['title']}").strip()[:80]
    target.mkdir(parents=True)
    for label, source in files.items():
        if source.is_file():
            shutil.copy2(source, target / label)
    os.startfile(target)
    return {"folder": str(target)}


CHROME = [pathlib.Path(p) / "Google/Chrome/Application/chrome.exe"
          for p in ("C:/Program Files", "C:/Program Files (x86)", pathlib.Path.home() / "AppData/Local")]


def open_forms(ids: list[str], options) -> dict:
    """Open each selected job's application form in Chrome, where the extension fills it.

    Chrome rather than the default browser, because the extension is installed there. A job is
    refused before opening when it has no approved files or is no longer open, so every tab
    that does open can be filled completely.
    """
    import subprocess
    import forms

    connection = store.connect(options.db)
    settled = store.settled_ids(connection)
    opened, refused = [], []
    chrome = next((str(p) for p in CHROME if p.is_file()), None)
    for identifier in ids[:BATCH_LIMIT]:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            continue
        job, name = dict(row), f"{row['company']} · {row['title']}"
        if identifier in settled:
            refused.append({"name": name, "why": "כבר הוגשה או הוסרה"})
            continue
        if not (pathlib.Path(options.ready) / f"{store.job_stem(job)}-cv.pdf").is_file():
            refused.append({"name": name, "why": "אין קבצים מאושרים, אשר והפק קודם"})
            continue
        if "myworkdayjobs.com" in (job["url"] or ""):
            # Workday's application starts at the posting's /apply page, after signing in to that company.
            url = job["url"].rstrip("/") + "/apply"
        elif "lever.co/" in (job["url"] or ""):
            # Lever's form is the posting's /apply page, open to anyone, with no account.
            url = job["url"].rstrip("/") + "/apply"
        elif "smartrecruiters.com/" in (job["url"] or ""):
            # SmartRecruiters' form page names the posting by its publication id, which its API gives.
            import smartrecruiters
            company, number = job["url"].rstrip("/").split("/")[-2:]
            try:
                posting = smartrecruiters.request(smartrecruiters.POSTING.format(company=company, posting=number))
            except Exception as error:
                refused.append({"name": name, "why": f"SmartRecruiters לא ענה: {str(error)[:60]}"})
                continue
            url = f"https://jobs.smartrecruiters.com/oneclick-ui/company/{company}/publication/{posting['uuid']}"
        else:
            try:
                url = forms.form_url(job)
            except forms.FormError as error:
                refused.append({"name": name, "why": str(error)})
                continue
        # The mark tells the extension this form was opened to be filled; a posting read by hand has none.
        url += "#job-agent-fill"
        subprocess.Popen([chrome, url]) if chrome else webbrowser.open(url)
        opened.append(name)
    connection.close()
    return {"opened": opened, "refused": refused}


def make_handler(options):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # the page has its own log, and the console stays readable

        def reply(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def answer(self, code: int, payload: dict) -> None:
            self.reply(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")

        def trusted_host(self) -> bool:
            """Refuse a request addressed to another name, which is how a site rebinds its own name to this machine."""
            return self.headers.get("Host", "") in (f"127.0.0.1:{options.port}", f"localhost:{options.port}")

        def trusted_origin(self) -> bool:
            """Accept a change only from this page or from the form extension, never from another site.

            Any site open in the browser can send a request here, and the job ids are derived from
            public links, so without this check a page elsewhere could press the submit button.
            A request with no Origin comes from a program on this machine, not from a web page.
            """
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            if origin in (f"http://127.0.0.1:{options.port}", f"http://localhost:{options.port}"):
                return True
            return origin.startswith("chrome-extension://") and self.path.startswith("/api/form/")

        def do_GET(self):
            if not self.trusted_host():
                return self.reply(403, b"forbidden", "text/plain")

            if self.path.startswith("/api/form/"):
                return self.form_get()

            if self.path.startswith("/api/mail-status"):
                connection = store.connect(options.db)
                checked = tracking.last_mail_check(connection)
                connection.close()
                with MAIL_LOCK:
                    state = dict(MAIL)
                return self.answer(200, {**state, "checked": checked})

            if self.path.startswith("/api/status"):
                with LOCK:
                    payload = json.dumps(STATE)
                return self.reply(200, payload.encode("utf-8"), "application/json")

            if self.path in ("/browser.js", "/tracking.js"):
                script = pathlib.Path(self.path.lstrip("/")).read_bytes()
                return self.reply(200, script, "text/javascript; charset=utf-8")

            if self.path.startswith("/tracking"):
                return self.reply(200, tracking.page(options.db).encode("utf-8"), "text/html; charset=utf-8")

            if self.path.startswith("/sources"):
                return self.reply(200, sources.page(options.db).encode("utf-8"), "text/html; charset=utf-8")

            folder = self.path.lstrip("/").split("/", 1)[0]
            if folder in (options.drafts, options.ready, options.sent):
                target = pathlib.Path(urllib.parse.unquote(self.path.lstrip("/"))).resolve()
                # Refuse anything that climbs out of the folder it names.
                if target.is_file() and target.is_relative_to(pathlib.Path(folder).resolve()):
                    kind = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
                    return self.reply(200, target.read_bytes(), kind)
                return self.reply(404, b"not found", "text/plain")

            # Rebuild on every load, so newly written documents appear without a restart.
            report.write(options.db, options.profile, "report.html", options.threshold, False, False, live=True)
            page = pathlib.Path("report.html").read_text(encoding="utf-8")
            page = page.replace("</body>", '<script src="/browser.js"></script></body>')
            return self.reply(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def form_get(self):
            """Serve the extension a job's fill plan, or one of the files the plan names."""
            import forms
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            connection = store.connect(options.db)
            try:
                if self.path.startswith("/api/form/plan"):
                    return self.answer(200, forms.plan(connection, query.get("url", [""])[0], pathlib.Path(options.ready)))
                if self.path.startswith("/api/form/drafts"):
                    return self.answer(200, forms.drafts(connection, query.get("job", [""])[0],
                                                         pathlib.Path(options.ready), options.model))
                if self.path.startswith("/api/form/file"):
                    path = forms.file_path(connection, query.get("job", [""])[0], query.get("kind", [""])[0],
                                           pathlib.Path(options.ready))
                    if path is None:
                        return self.reply(404, b"not found", "text/plain")
                    return self.reply(200, path.read_bytes(), "application/pdf")
                return self.reply(404, b"not found", "text/plain")
            except forms.FormError as error:
                return self.answer(409, {"error": str(error)})
            except Exception as error:
                return self.answer(502, {"error": f"לא הצלחתי להכין את הטופס: {str(error)[:120]}"})
            finally:
                connection.close()

        def do_POST(self):
            # A JSON content type cannot be sent across sites without the browser asking first,
            # and this server never says yes, so together with the origin check no other site gets in.
            if not self.trusted_host() or not self.trusted_origin() \
                    or not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.reply(403, b'{"error":"forbidden"}', "application/json")
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.reply(400, b'{"error":"bad request"}', "application/json")

            if self.path == "/api/form/answers":
                import forms
                connection = store.connect(options.db)
                try:
                    return self.answer(200, forms.answer_questions(connection, payload.get("url", ""),
                                                                   payload.get("questions", []), pathlib.Path(options.ready)))
                except forms.FormError as error:
                    return self.answer(409, {"error": str(error)})
                finally:
                    connection.close()

            if self.path == "/api/form/remember":
                import forms
                connection = store.connect(options.db)
                try:
                    job = connection.execute("SELECT * FROM jobs WHERE id = ?", (payload.get("job_id", ""),)).fetchone()
                    if job is None:
                        return self.answer(404, {"error": "המשרה לא נמצאה במאגר"})
                    return self.answer(200, {"kept": forms.remember(connection, dict(job), payload.get("values", []))})
                finally:
                    connection.close()

            if self.path == "/api/form/folder":
                answer = upload_folder(payload.get("job_id", ""), options)
                return self.answer(409 if "error" in answer else 200, answer)

            if self.path == "/api/form/submitted":
                import forms
                connection = store.connect(options.db)
                try:
                    application = forms.record(connection, payload.get("job_id", ""), payload.get("values", []), options)
                    return self.answer(200, {"application": application})
                except (forms.FormError, LookupError, ValueError) as error:
                    return self.answer(409, {"error": str(error)})
                finally:
                    connection.close()

            if self.path == "/api/upload-folder":
                answer = upload_folder(payload.get("job_id", ""), options)
                return self.answer(409 if "error" in answer else 200, answer)

            if self.path == "/api/mail-check":
                return self.answer(200, mail_check(options, bool(payload.get("force"))))

            if self.path == "/api/open-forms":
                return self.answer(200, open_forms(payload.get("ids", []), options))

            # Tracking changes are small database writes, so they answer at once instead of queueing.
            if self.path in TRACKING_ACTIONS:
                code, answer = tracking.handle(TRACKING_ACTIONS[self.path], payload, options)
                return self.reply(code, json.dumps(answer, ensure_ascii=False).encode("utf-8"), "application/json")

            # Shows what the submit button would send, and sends nothing.
            if self.path == "/api/submit-preview":
                import submit
                try:
                    answer = submit.preview(payload.get("job_id", ""), options)
                    code = 200
                except submit.SubmitError as error:
                    answer, code = {"error": str(error)}, 409
                except Exception as error:
                    answer, code = {"error": f"הבדיקה נכשלה: {str(error)[:120]}"}, 502
                return self.reply(code, json.dumps(answer, ensure_ascii=False).encode("utf-8"), "application/json")

            ids = payload.get("ids", [])
            with LOCK:
                busy = STATE["running"]
            if busy:
                return self.reply(409, b'{"error":"busy"}', "application/json")

            if self.path == "/api/submit":
                ids, action = [payload.get("job_id", "")], "submit"
            elif self.path == "/api/submit-batch":
                action = "submit"
            else:
                action = "approve" if self.path.startswith("/api/approve") else "tailor"
            threading.Thread(target=work, args=(action, ids, options), daemon=True).start()
            return self.reply(202, b'{"started":true}', "application/json")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the report so its buttons work.")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--db", default=store.DB_PATH)
    parser.add_argument("--profile", default="profile.md")
    parser.add_argument("--facts", default="facts.md")
    parser.add_argument("--drafts", default="applications")
    parser.add_argument("--ready", default="ready")
    parser.add_argument("--sent", default="sent", help="where a copy of each sent application is kept")
    parser.add_argument("--threshold", type=int, default=50)
    parser.add_argument("--model", default=tailor.MODEL_CLI)
    parser.add_argument("--no-open", action="store_true")
    options = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    address = ("127.0.0.1", options.port)
    # Windows lets a second server bind a port that address reuse leaves open, and the two then
    # answer requests in turn, one of them with old code. Refuse to start instead.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        server = ThreadingHTTPServer(address, make_handler(options))
    except OSError:
        print(f"port {options.port} is already in use, probably by serve.py running in another window")
        return 1
    # The page asks each company's board whether its listed jobs closed, once an hour, which took the
    # first load of each hour about nine seconds. Asking in the background keeps every load quick.
    def keep_closed_jobs_fresh():
        import tempfile
        import time
        while True:
            try:
                report.write(options.db, options.profile, str(pathlib.Path(tempfile.gettempdir()) / "job-agent-warm.html"),
                             options.threshold, False, False, live=True)
            except Exception as error:
                print(f"background check of closed jobs failed: {error}")
            time.sleep(50 * 60)

    threading.Thread(target=keep_closed_jobs_fresh, daemon=True).start()
    url = f"http://127.0.0.1:{options.port}/"
    print(f"serving {url}")
    print("loopback only, nothing on the network. Ctrl+C to stop.")
    if not options.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
