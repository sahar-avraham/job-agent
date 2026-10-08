# What to run

The short version. `README.md` has the long one.

Everything runs from the project folder with plain `python`. The scripts that need
extra packages switch to the project's `.venv` by themselves (`use_venv.py`), so
any terminal works.

First time on a new machine only:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## The normal day

**Look for new jobs.** First checks the Israeli Tech Map for companies hiring in your
fields that no board reads, and adds the ones it can read. Then collects, filters, scores
whatever is new (up to 25 a run), tailors a draft CV and letter for up to 10 jobs above 50 that
have none, and writes the report. Postings older than six months are dropped, and ones older
than three months carry a badge. A company already applied to gets no new draft, and neither
does a job known only from the Tech Map. Approving a draft into Word and PDF files stays a
click on the page. `--no-tailor` skips the drafts, `--no-scout` the company check, and
`--limit 100` scores more in one run.

```
python run.py
```

**Open the report with working buttons.** Pick jobs, tailor CVs, make Word files,
mark what you sent, track it. Ctrl+C to stop. Opening the tracking page reads the
mailbox when it was not read in the last hour, and its button reads it now.

```
python serve.py
```

That's most of it. The rest is for when you need something specific.

## Around applications

| I want to | Run |
|---|---|
| Tailor CVs for the top jobs, no browser | `python tailor.py` |
| Tailor just one job | `python tailor.py --only "salt"` |
| Tailor every job above 50 still without a draft | `python tailor.py --missing --limit 20` |
| Re-check existing drafts for invented facts | `python tailor.py --audit-only` |
| Turn one draft into Word files | `python approve.py "salt security"` |
| See where every application stands | `python tracking.py` |
| Same, as JSON for a script or Claude | `python tracking.py --json` |

Drafts land in `applications/`, Word files in `ready/`, and a copy of whatever
you marked as sent in `sent/`.

**Submitting from the page.** Each card has a green "הגש" button. It works for Comeet
jobs that already have approved Word files: it shows where the mail goes and what is
attached, and sends from your Gmail only when you confirm. To send several at once,
tick them and press "הגש את המסומנות" in the bar at the top: it checks each one, lists
what will and will not go, and after one confirm sends up to 10, twenty seconds apart.
The same without the page:

```
python submit.py <job id> --preview
python submit.py <job id>
```

**Application forms.** Tick approved Greenhouse, Workday, Lever or SmartRecruiters jobs
and press "פתח ומלא טפסים בכרום". Each form opens in Chrome and the job-agent extension fills
it: details, the approved CV and letter, experience and education, and every question
`answers.md` or an earlier form can answer. A panel on the form lists what is left for you.
An answer you correct by hand is remembered for later forms. Read each step, press its own
Next and Submit buttons; the application is recorded when the confirmation appears. Needs `serve.py` running, and the extension
loaded once: `chrome://extensions`, Developer mode, "Load unpacked", choose the
`extension` folder. After changing its files, press reload on its card.

## Mail

`run.py` reads the mailbox by itself once it is set up, so there is usually nothing
to run. Application receipts become applications, replies become updates, and
anything unclear waits on the tracking page for a yes or no.

| I want to | Run |
|---|---|
| Check the login works, no Claude calls | `python mail_sync.py --check` |
| Read the mail now, without collecting jobs | `python mail_sync.py` |
| Look further back on the first run | `python mail_sync.py --days 365 --limit 200` |
| Run the pipeline without touching mail | `python run.py --no-mail` |

Setup, once: turn on 2-Step Verification for the Google account, create an app
password at myaccount.google.com/apppasswords, and put two lines in `.env` in this
folder. `.env` is ignored by git.

```
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

## Looking around

| I want to | Run |
|---|---|
| Collect and filter, but ask Claude nothing | `python run.py --dry-run` |
| Numbers from the database, plus the top 10 | `python store.py --top 10` |
| See why the filter dropped each job | `python filter_jobs.py --explain` |
| Rewrite the report without opening a server | `python report.py --open` |
| Check Claude is reachable at all | `python claude_cli.py` |

The last one hangs inside a Claude Code session. Run it in a normal terminal.

## Once in a while

**More companies.** Every few weeks. Takes about fifteen minutes, then adds what
it found to `companies.json`, the private list every run reads.

```
python catalogue.py --refresh --sweep --save
```

**More Israeli companies on Comeet.** Same idea, different source. The first
command reads new company addresses from Common Crawl, the second checks them and
adds the ones hiring locally to `companies.json`.

```
python comeet.py --collect --crawls 3
python comeet.py --sweep --save
```

**A job you found yourself on a company's own careers site.** Paste its link. If
the site runs on Comeet, this adds the company.

```
python comeet.py --add https://www.example.co.il/careers/some-job/ --save
```

**Workday companies hiring in Israel.** Every few weeks, like the other catalogues. The sweep asks
about 13,000 Workday sites, about 40 minutes; --save adds the ones with jobs in Israel.

```
python workday.py --sweep
python workday.py --save
```

**Israeli companies from the Israeli Tech Map.** Every few weeks. Downloads the map's company
list and daily job list, checks every Comeet, Greenhouse, Lever, Ashby or Workday board it names
that is not tracked yet, and adds the ones with jobs in Israel. A few minutes.

```
python techmap.py --save
```

**SmartRecruiters companies hiring in Israel.** Every few weeks. Searches Israeli cities on
SmartRecruiters' own job search and lists the companies found; `--skip` leaves out ones not in tech.

```
python smartrecruiters.py
python smartrecruiters.py --save --skip SomeIdentifier
```

**Lever only, after a change to how its boards are checked.**

```
python catalogue.py --sweep --board lever --again --save
```

**One company the catalogue doesn't know.**

```
python discover_boards.py "Company Name"
```

**Companies the Tech Map says are hiring, checked again.** `run.py` checks new ones by
itself; this checks every company again and prints the table the sources page shows.

```
python scout.py --again
```

**After editing `profile.md`.** Nothing to run on purpose. The next `run.py`
notices the change and scores again under the new rules, up to 25 jobs a run.

## Checking the scoring is still sane

Only when you change the rubric and want proof, not a feeling.

```
python rate_shortlist.py
python rate_shortlist.py --report
```

Five minutes, one question per job, and the report says where you and the model
disagree. The bigger version is `make_evalset.py`, `rate_jobs.py` and
`calibrate.py`, described in the README.

## When something breaks

- **`No module named 'pydantic'`** means `.venv` is missing or empty. Run the two
  first-time commands at the top.
- **401 or authentication error** means Claude's login expired. Run `claude` in a
  terminal and log in again.
- **The buttons do nothing** means the page was opened as a file. Use `serve.py`.
- **Port already in use** means an old `serve.py` is still running. Close that
  terminal, or pass `--port 8778`.
