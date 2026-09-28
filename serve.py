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
import sys
import threading
import urllib.parse
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import report
import store
import tailor
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

        stem = tailor.re.sub(r"[^a-z0-9]+", "-", f"{job['company']}-{job['title']}".lower()).strip("-")[:60]
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
        stem = tailor.re.sub(r"[^a-z0-9]+", "-", f"{job.get('company','')}-{job.get('title','')}".lower()).strip("-")[:60]
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

        def do_GET(self):
            if self.path.startswith("/api/status"):
                with LOCK:
                    payload = json.dumps(STATE)
                return self.reply(200, payload.encode("utf-8"), "application/json")

            if self.path in ("/browser.js", "/tracking.js"):
                script = pathlib.Path(self.path.lstrip("/")).read_bytes()
                return self.reply(200, script, "text/javascript; charset=utf-8")

            if self.path.startswith("/tracking"):
                return self.reply(200, tracking.page(options.db).encode("utf-8"), "text/html; charset=utf-8")

            folder = self.path.lstrip("/").split("/", 1)[0]
            if folder in (options.drafts, options.ready, options.sent):
                target = pathlib.Path(urllib.parse.unquote(self.path.lstrip("/"))).resolve()
                # Refuse anything that climbs out of the folder it names.
                if target.is_file() and target.is_relative_to(pathlib.Path(folder).resolve()):
                    kind = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
                    return self.reply(200, target.read_bytes(), kind)
                return self.reply(404, b"not found", "text/plain")

            # Rebuild on every load, so newly written documents appear without a restart.
            report.write(options.db, options.profile, "report.html", options.threshold, False, False)
            page = pathlib.Path("report.html").read_text(encoding="utf-8")
            page = page.replace("</body>", '<script src="/browser.js"></script></body>')
            return self.reply(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.reply(400, b'{"error":"bad request"}', "application/json")

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
    server = ThreadingHTTPServer(address, make_handler(options))
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
