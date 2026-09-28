# job-agent

A personal job-search pipeline. It collects open positions from the public feeds of
four applicant tracking systems, drops most of them with plain rules, scores the rest
against a written profile with an LLM, and prepares a tailored CV and cover letter for
the ones worth applying to. A local web page is where the candidate reads, approves and
applies, and a mailbox reader keeps track of what happened to each application.

Nothing is sent without a click. The model never writes a claim of its own: it picks
from a file of facts, and a second, independent pass blocks any sentence the facts do
not support.

What to run day to day is in [COMMANDS.md](COMMANDS.md).

## How it works

```
public ATS feeds ──> collect ──> hard rules ──> LLM score ──> report page
 Greenhouse, Lever,   ~2,800      ~170 pass      only new       read, approve,
 Ashby, Comeet        open jobs   (no model)     jobs           submit
                                                   │
                                        tailor: select facts ──> verify ──> Word + PDF
                                                                       │
                                  mailbox (read-only) ──> classify replies ──> tracking page
```

| Stage | File | What it does |
|---|---|---|
| Collect | `fetch_jobs.py` | Adapters for four ATS feeds, normalized into one job shape |
| Find companies | `catalogue.py`, `comeet.py` | Sweep public board catalogues and the Common Crawl index for companies hiring locally |
| Hard rules | `filter_jobs.py` | Title, seniority, discipline, degree, years and commute rules, no model |
| Score | `score_job.py` | One model call per job, validated against a pydantic schema |
| Store | `store.py` | SQLite; scores keyed by job and a fingerprint of the profile |
| Tailor | `tailor.py`, `cv_layout.py` | Select fact ids for a fixed CV layout, then an independent verification pass |
| Coverage | `keywords.py` | Compare the posting's technologies with the tailored CV, in plain code |
| Render | `render_docx.py` | Word from a fixed template, PDF through the installed Word |
| Review | `serve.py`, `report.py`, `browser.js` | Local page: read, approve, remove, submit |
| Submit | `submit.py` | Email application to the position's Comeet address, after a confirm |
| Track | `mail_sync.py`, `tracking.py` | Read replies over IMAP, classify, append to an event log |
| Evaluate | `make_evalset.py`, `rate_jobs.py`, `rate_shortlist.py`, `calibrate.py`, `filter_review.py` | Hand-labelled sets and breakdowns used to tune the rules |

## Decisions worth knowing

- **Rules before the model.** Of about 2,800 open positions, around 170 pass the hard
  rules. The model only ever sees those, and only the ones it has not scored before.
- **The model chooses, it does not write.** A tailored CV is a selection of fact ids
  from `facts.md`, printed in a fixed layout. The only free text is the cover note, and
  it goes through the same verification as everything else.
- **Verification is repeated before anything is sent**, because a model check is not
  perfectly consistent: a document that passed once can fail the same check again.
- **State is derived, not stored.** An application's stage is the latest event in its
  log, so a wrong update is corrected by adding the right one.
- **Measured, not guessed.** Scoring rules were tuned against hand-labelled jobs, and
  every filter rule added in batches was checked against the scores already collected.
- **The model runs through the Claude Code CLI** on a subscription, so there is no API
  key in the project. `claude_cli.py` wraps it; an API backend is also supported.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Then copy the four example files and fill them in with your own details:

```
.env.example        ->  .env          name, contact line, Gmail app password
facts.example.md    ->  facts.md      every claim a CV may contain
profile.example.md  ->  profile.md    preferences and the scoring rubric
answers.example.md  ->  answers.md    standing answers for application forms
```

None of those four files, the database, or any generated document is ever committed.
`publish.py` builds the public copy of the project from an explicit list of files and
refuses to finish if any private value appears in it.

## Why the company list is not in the repository

The list of companies to read lives in `companies.json`, which is never committed.
`companies.example.json` shows its shape with three companies on documented public APIs.
Your own list is built by the scripts, not copied from here:

```
python catalogue.py --refresh --sweep --save     Greenhouse, Lever and Ashby
python comeet.py --collect                       Comeet career pages, from Common Crawl
python comeet.py --sweep --save
```

This is a deliberate decision, and it is about Comeet. Greenhouse, Lever and Ashby
publish job-board APIs meant for exactly this use, and public catalogues of their
board identifiers already exist. Comeet is different: there is no public directory
of its customers, and reading a company's positions needs that company's own token.
The token is visible in the source of the company's careers page, which uses it to
show its jobs, so reading it there to see the same jobs a visitor sees is reasonable.
Collecting hundreds of those tokens into one published file is not: each one belongs
to its company, and a bulk list of them is not ours to distribute. So the code that
finds them is public, and what it finds stays on the machine that ran it.

`publish.py` enforces this too: it refuses to build the public copy if any collected
Comeet token appears in it.

## Limits

- Windows-first: PDF export drives the installed Microsoft Word.
- Only Comeet positions can be submitted from the page; other boards need their forms.
- There is no automated test suite yet. The evaluation sets measure the scoring, not
  the code.
