# job-agent

A personal job-search pipeline. It collects open positions from the public feeds of
seven applicant tracking systems, the careers sites of a few large employers and the
Israeli Tech Map, drops most of them with plain rules, scores the rest against a written
profile with an LLM, and prepares a tailored CV and cover letter for the ones worth
applying to. A local web page is where the candidate reads, approves and applies, a
Chrome extension fills the application forms, and a mailbox reader keeps track of what
happened to each application.

Nothing is sent without a click. The model never writes a claim of its own: it picks
from a file of facts, and a second, independent pass blocks any sentence the facts do
not support.

What to run day to day is in [COMMANDS.md](COMMANDS.md).

## How it works

```
company discovery ──> public ATS feeds ──> collect ──> hard rules ──> LLM score ──> report page
 Tech Map job lists    Greenhouse, Lever,   ~6,100      ~450 pass     only new       read, approve,
 + careers pages       Ashby, Comeet,       open jobs   (no model)    jobs           apply
                       Workday, Workable,                                │
                       SmartRecruiters,                   tailor: select facts ──> verify ──> Word + PDF
                       large employers                                                  │
                                                     extension fills the form <── approved files
                                                                                        │
                                       mailbox (read-only) ──> classify replies ──> tracking page
```

| Stage | File | What it does |
|---|---|---|
| Collect | `fetch_jobs.py` | Adapters for the ATS feeds, normalized into one job shape with its posting date |
| More boards | `workday.py`, `smartrecruiters.py`, `employers.py` | Workday sites, SmartRecruiters companies, and large employers on systems of their own (Amazon, Google, Eightfold, Oracle Recruiting Cloud, Bob, Elbit) |
| Find companies | `catalogue.py`, `comeet.py`, `techmap.py` | Sweep public board catalogues, the Common Crawl index and the Israeli Tech Map for companies hiring locally |
| Discover | `scout.py`, `devjobs.py`, `linkedin.py` | Every run: companies the Tech Map lists as hiring in the wanted fields that no board reads; their careers page is checked for a known system, else their jobs are taken from the map's own rows, with each job's full text looked up by its number |
| Hard rules | `filter_jobs.py` | Title, seniority, discipline, degree, years, posting age and commute rules, no model |
| Score | `score_job.py` | One model call per job, validated against a pydantic schema |
| Store | `store.py` | SQLite; scores keyed by job and a fingerprint of the profile |
| Tailor | `tailor.py`, `cv_layout.py` | Select fact ids for a fixed CV layout, then an independent verification pass |
| Coverage | `keywords.py` | Compare the posting's technologies with the tailored CV, in plain code |
| Render | `render_docx.py` | Word from a fixed template, PDF through the installed Word |
| Review | `serve.py`, `report.py`, `browser.js`, `sources.py` | Local pages: jobs to read, approve and remove; the sources page, with failing boards and discovered companies |
| Apply | `forms.py`, `extension/` | Plan each form's answers and fill Greenhouse, Workday, Lever and SmartRecruiters forms in the candidate's Chrome |
| Submit | `submit.py` | Email application to the position's Comeet address, after a confirm |
| Track | `mail_sync.py`, `tracking.py` | Read replies over IMAP, classify, append to an event log |
| Evaluate | `make_evalset.py`, `rate_jobs.py`, `rate_shortlist.py`, `calibrate.py`, `filter_review.py` | Hand-labelled sets and breakdowns used to tune the rules |

## Decisions worth knowing

- **Rules before the model.** Of about 6,100 open positions, around 450 pass the hard
  rules. The model only ever sees those, and only the ones it has not scored before.
- **Find companies by who is hiring, not by who is famous.** The Tech Map's daily job
  lists say which companies hire in the wanted fields. One that no board reads has its
  careers page searched for a known hiring system and is added when it has jobs here;
  one that cannot be read keeps its jobs from the map's rows. Once every board is read, a map job
  that a board already gave is dropped as a duplicate, and the rest get their full text by the
  posting's LinkedIn number: from devjobs.co.il, else from LinkedIn's public job page, read one at
  a time, a few seconds apart, and not at all for a day after any refusal. A job whose text is not
  found is listed but never scored, since a score from a title alone is a guess.
- **The model chooses, it does not write.** A tailored CV is a selection of fact ids
  from `facts.md`, printed in a fixed layout. The cover letter is fixed lines the
  candidate wrote, kept in `facts.md`. The model only names the company's field when it is
  new to the candidate, and that line is checked like everything else. A model-written
  sentence about the projects was tried and dropped: it repeated the CV and read as
  machine-written.
- **Forms are filled, never sent.** Greenhouse, Workday, Lever and SmartRecruiters forms
  are filled in the candidate's own Chrome by the extension in `extension/`, and the
  candidate presses every Next and Submit. An answer corrected by hand on a form replaces
  the earlier one and is remembered for every later form, on any of those systems. See
  `extension/README.md` for why an extension rather than an automated browser.
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

Then copy the example files and fill them in with your own details:

```
.env.example            ->  .env            name, contact line, home city, Gmail app password
facts.example.md        ->  facts.md        every claim a CV may contain
profile.example.md      ->  profile.md      preferences and the scoring rubric
answers.example.md      ->  answers.md      standing answers for application forms
commute.example.json    ->  commute.json    the places inside and outside your commute area
```

`commute.json` lists the places you can commute to and the ones you cannot, in every
spelling job boards use, English and Hebrew. A job is dropped only when its location names
a place outside the area and none inside it. Set it to your own area; without the file, no
job is dropped for its location.

None of those files, the database, or any generated document is ever committed.
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
python workday.py --sweep                        Workday sites hiring in Israel
python workday.py --save
python smartrecruiters.py --save                 SmartRecruiters companies hiring in Israel
python techmap.py --save                         boards named in the Israeli Tech Map
```

After that, `run.py` keeps the list growing by itself through `scout.py`.

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

Sites behind bot protection (a JavaScript or CAPTCHA challenge) are left alone; their
jobs come from the Tech Map's rows when it lists them.

## Limits

- Windows-first: PDF export drives the installed Microsoft Word.
- Only Comeet positions can be submitted from the page. Forms on Greenhouse, Workday,
  Lever and SmartRecruiters are filled by the extension and sent by the candidate; other
  systems are filled by hand.
- Jobs known only from the Tech Map wait unscored until their full text is found.
- Tests cover only the readers of other sites (`python -m unittest discover tests`), on short pages
  written to their shape. The evaluation sets measure the scoring.
- LinkedIn's public job page gives a job's text but not where to apply outside LinkedIn, which it
  shows only to members who sign in; that link is not read.
