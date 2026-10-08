"""Turn one draft into Word files you stand behind.

Everything in applications/ is a draft. This is the step that makes a document
ready to send, and it is deliberate rather than automatic for one reason: the
audit is a model, so it is not perfectly consistent. A document that passed once
is a document that was not caught that time, which is not the same as clean.

So approval re-audits, shows you what the employer will read, and only writes the
Word files after you say yes. What lands in ready/ is what you have read.
"""

from __future__ import annotations

import use_venv  # noqa: F401, must come first: relaunches under .venv when started with another Python

import argparse
import json
import pathlib
import re
import sys

import claude_cli
import render_docx
import tailor


def show(selection: dict, facts: dict[str, str]) -> None:
    """Print the parts that vary, because the fixed parts are the same every time."""
    chosen, job = selection["chosen"], selection["job"]
    print("=" * 78)
    print(f"{job.get('company', '')} - {job.get('title', '')}")
    print(job.get("url", ""))
    print("=" * 78)

    print(f"\nHEADLINE\n  {facts.get(chosen['headline_id'], '')}")
    print("\nOPENS WITH " + chosen.get("lead_with", "").upper())
    for key, label in (("experience_ids", "EXPERIENCE"), ("project_ids", "PROJECTS")):
        items = [facts[i] for i in chosen.get(key, []) if i in facts]
        if items:
            print(f"\n{label}, {len(items)} lines, first two:")
            for item in items[:2]:
                print(f"  - {item[:110]}")
    print(f"\nSKILLS\n  {', '.join(facts[i] for i in chosen.get('skill_ids', []) if i in facts)[:200]}")
    print(f"\nCOVER LETTER\n  {chosen['cover_note']}")


def reaudit(selection: dict, facts: dict[str, str], folder: pathlib.Path, stem: str, model: str) -> bool:
    """Check the text that is about to be written, and report what it says.

    The files are built from the selection and today's facts, so a draft written before a fact changed
    would be checked on its old wording. The text and the letter are rebuilt from today's facts first,
    and the draft on the page is rewritten to match, so what is read, checked and sent are the same.
    """
    # A draft from before the fixed letter has no new_field; it gets the fixed letter without that line.
    chosen = tailor.Tailored.model_validate({"new_field": "", **selection["chosen"]})
    chosen.cover_note = tailor.cover_letter(chosen, facts, selection["job"])
    selection["chosen"]["cover_note"] = chosen.cover_note
    document = tailor.document_text(chosen, facts, selection["job"])
    (folder / f"{stem}.txt").write_text(document, encoding="utf-8")
    (folder / f"{stem}.html").write_text(tailor.render_html(chosen, facts, selection["job"]), encoding="utf-8")
    (folder / f"{stem}.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    text, _ = claude_cli.ask(
        tailor.AUDIT_PROMPT.format(
            facts=tailor.facts_block(facts),
            document=document,
            schema=json.dumps(tailor.Audit.model_json_schema(), indent=2),
        ),
        model=model,
    )
    audit = tailor.Audit.model_validate(claude_cli.extract_json(text))
    clean = not audit.unsupported and not audit.exaggerations

    print("\nAUDIT")
    if clean:
        print("  nothing unsupported found this time")
    for item in audit.unsupported:
        print(f"  unsupported: {item}")
    for item in audit.exaggerations:
        print(f"  stretched:   {item}")
    return clean


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve one draft and write it as Word files.")
    parser.add_argument("match", help="part of the company or job title, enough to pick one draft")
    parser.add_argument("--drafts", default="applications")
    parser.add_argument("--ready", default="ready", help="where approved documents go")
    parser.add_argument("--facts", default="facts.md")
    parser.add_argument("--model", default=tailor.MODEL_CLI)
    parser.add_argument("--no-audit", action="store_true", help="skip the re-check, only when you have just run it")
    parser.add_argument("--yes", action="store_true", help="do not ask, for when you have already read the draft")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    drafts = pathlib.Path(args.drafts)
    # File names use hyphens, so compare word by word rather than as one string.
    wanted = [w for w in re.split(r"[^a-z0-9]+", args.match.lower()) if w]
    matches = [
        p for p in sorted(drafts.glob("*.json"))
        if all(word in re.split(r"[^a-z0-9]+", p.stem.lower()) for word in wanted)
    ]

    if not matches:
        print(f"no draft matching {args.match!r}. Available:", file=sys.stderr)
        for path in sorted(drafts.glob("*.json")):
            print(f"  {path.stem}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"{args.match!r} matches more than one draft, be more specific:", file=sys.stderr)
        for path in matches:
            print(f"  {path.stem}", file=sys.stderr)
        return 1

    path = matches[0]
    selection = json.loads(path.read_text(encoding="utf-8"))
    facts = tailor.load_facts(pathlib.Path(args.facts))

    show(selection, facts)
    clean = True if args.no_audit else reaudit(selection, facts, drafts, path.stem, args.model)

    if not args.yes:
        prompt = "\napprove and write the Word files? [y/N] > " if clean else \
                 "\nthe audit flagged something above. Approve anyway? [y/N] > "
        if input(prompt).strip().lower() not in {"y", "yes"}:
            print("nothing written")
            return 0

    written = render_docx.render_both(selection["chosen"], facts, selection["job"],
                                      pathlib.Path(args.ready), path.stem)
    print()
    for file in written:
        print(f"wrote {file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
