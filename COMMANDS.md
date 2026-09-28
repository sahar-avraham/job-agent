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

**Look for new jobs.** Collects, filters, scores whatever is new, writes the report.

```
python run.py
```

**Open the report with working buttons.** Pick jobs, tailor CVs, make Word files,
mark what you sent, track it. Ctrl+C to stop.

```
python serve.py
```

That's most of it. The rest is for when you need something specific.

## Around applications

| I want to | Run |
|---|---|
| Tailor CVs for the top jobs, no browser | `python tailor.py` |
| Tailor just one job | `python tailor.py --only "salt"` |
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

**One company the catalogue doesn't know.**

```
python discover_boards.py "Company Name"
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
