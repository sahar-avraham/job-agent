"""Ask Claude a question through the Claude Code command line instead of the API.

The Claude Code login is a subscription, billed monthly, while the API is a
separate product billed per token. Running the model through the command line
therefore costs nothing beyond the subscription already paid for.

Two things are given up in exchange. The reply is plain text rather than a
schema-validated object, so the JSON contract is stated in the prompt and checked
here. And every call starts a fresh process, which is slower than an API request.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 240

# Nothing here needs a tool, and denying them keeps the call to a single turn.
DISALLOWED = "Bash Edit Write Read Glob Grep WebSearch WebFetch Task NotebookEdit"


class ClaudeCliError(RuntimeError):
    """Raised when the command line cannot be reached or answers with something unusable."""


def resolve_command() -> list[str]:
    """Work out how to launch Claude Code on this machine.

    On Windows the installed `claude` is a .CMD wrapper, and passing the bare name
    to a subprocess fails because the launcher only searches for an exact filename.
    Running the JavaScript entry point directly avoids the wrapper altogether, so
    that is tried first and the wrapper's full path is the fallback.
    """
    override = os.environ.get("CLAUDE_CLI")
    if override:
        return [override]

    launcher = shutil.which("claude")
    if launcher is None:
        raise ClaudeCliError(
            "the claude command was not found on PATH. Install Claude Code, "
            "set CLAUDE_CLI to its full path, or use --backend api"
        )

    node = shutil.which("node")
    entry = pathlib.Path(launcher).with_name("node_modules") / "@anthropic-ai" / "claude-code" / "cli.js"
    if node and entry.is_file():
        return [node, str(entry)]

    return [launcher]


def available() -> bool:
    """Report whether the command line can be launched, so callers can fail with advice."""
    try:
        resolve_command()
    except ClaudeCliError:
        return False
    return True


def ask(prompt: str, model: str = "opus", timeout: int = TIMEOUT_SECONDS) -> tuple[str, dict]:
    """Send one prompt and return the reply text along with the envelope around it.

    The prompt goes in on standard input rather than as an argument, because a job
    description easily exceeds the length a Windows command line accepts.
    """
    command = resolve_command() + [
        "-p",
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
        "--disallowedTools", DISALLOWED,
    ]

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ClaudeCliError(f"the claude command did not answer within {timeout} seconds") from error

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:400]
        raise ClaudeCliError(f"the claude command exited with {completed.returncode}: {detail}")

    envelope = json.loads(completed.stdout) if completed.stdout.strip() else {}
    if isinstance(envelope, dict) and envelope.get("is_error"):
        raise ClaudeCliError(f"claude reported an error: {str(envelope.get('result'))[:400]}")

    text = envelope.get("result", "") if isinstance(envelope, dict) else str(envelope)
    if not text:
        raise ClaudeCliError("the claude command answered with nothing")
    return text, envelope if isinstance(envelope, dict) else {}


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a reply that may be wrapped in prose or code fences."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None

    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ClaudeCliError(f"no JSON object in the reply: {text[:200]}")
        candidate = text[start : end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ClaudeCliError(f"the reply was not valid JSON: {candidate[:200]}") from error


def self_test() -> int:
    """Check that the command line answers at all, which is the first thing to try."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        print(f"launching: {' '.join(resolve_command())}")
    except ClaudeCliError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    try:
        text, envelope = ask('Reply with this exact JSON and nothing else: {"ok": true}')
    except ClaudeCliError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    print(f"reply: {text.strip()[:200]}")
    print(f"parsed: {extract_json(text)}")
    if "total_cost_usd" in envelope:
        print(f"reported cost: {envelope['total_cost_usd']} (notional under a subscription)")
    print("\nthe command line works, scoring can run through it")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
