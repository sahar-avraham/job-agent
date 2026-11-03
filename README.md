# job-agent

A personal job-search pipeline. Once a day it collects open positions, keeps the ones worth
reading, and drafts a tailored CV and cover letter for the best of them. You read, approve and
apply from a local web page, and it follows what happens to each application through your mailbox.

Two rules shape all of it:

- **Nothing is sent without a click.** The daily run only prepares; every application is yours to send.
- **The model never writes a claim of its own.** A CV is a selection of lines from a file of facts
  you wrote, and a second, independent pass blocks anything those facts do not support.

## How it flows

```
AUTOMATIC, ONCE A DAY

  Find companies   who is hiring in your fields that no board reads yet
        |
  Collect          open jobs from every board in your company list
        |
  Hard rules       your rules on title, seniority, years and commute; no model
        |
  Score            the model scores jobs it has not seen, up to 25 in one run
        |
  Draft            CV and letter for jobs above 50, up to 10 in one run,
                   then an independent check against your facts

WHAT YOU DO, ON THE JOBS PAGE

  Read             scores, reasons, drafts
        |
  Approve          turns a draft into Word and PDF files
        |
  Apply            by email, by a form the extension fills, or by hand

AFTER YOU APPLY

  Read mail        replies are read and classified, with no change to your mailbox
        |
  Follow           the tracking page shows each application's latest news
```

How many jobs reach you depends on your own rules: stricter rules leave fewer, looser rules leave
more. What this project fixes is where it reads from, listed under
[Where the jobs come from](#where-the-jobs-come-from).

## First setup

Most of the time goes to waiting for step 4.

**You need:** Windows, Python 3.11, Git, Microsoft Word (it makes the PDFs), Chrome, and
[Claude Code](https://claude.com/claude-code) signed in. A Gmail account with 2-Step Verification
is needed to read replies and to send applications by email.

Everything runs on your computer, except that posting text, your facts and the text of application
mail are sent to the model provider.

### 1. Install

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

After this, plain `python` works in any terminal: the scripts switch to `.venv` by themselves.

### 2. Write your files

Copy each example and fill it in.

| Copy | To | What goes in it |
|---|---|---|
| `.env.example` | `.env` | Name, contact line, home city, Gmail address and app password |
| `facts.example.md` | `facts.md` | Every line a CV or letter may contain, each with an id |
| `profile.example.md` | `profile.md` | Your preferences and the rubric jobs are scored by |
| `answers.example.md` | `answers.md` | Standing answers for application forms |
| `commute.example.json` | `commute.json` | Places you can and cannot commute to, in every spelling boards use |

`facts.md` and `profile.md` matter most: the first decides what your CV can say, the second decides
which jobs reach you. Without `commute.json`, no job is dropped for its location.

Then set the hard rules to your own search. They are the lists at the top of `filter_jobs.py`: the
titles, roles and disciplines to drop, and the most years of experience a job may ask for.

Create the Gmail app password at
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

### 3. Check the connections

```
python claude_cli.py
python mail_sync.py --check
```

Run the first from a normal terminal; it hangs inside a Claude Code session.

### 4. Build your company list

Your list lives in `companies.json`. Without it, only the three companies in
`companies.example.json` are read. These commands build it:

```
python catalogue.py --refresh --sweep --save
python comeet.py --collect
python comeet.py --sweep --save
python workday.py --sweep
python workday.py --save
python smartrecruiters.py --save
python techmap.py --save
```

The first takes about 15 minutes and `workday.py --sweep` about 40. After this the daily run keeps
the list growing by itself.

### 5. Run it once

```
python run.py
python serve.py
```

Open [127.0.0.1:8777](http://127.0.0.1:8777). Keep that terminal open while you use the page, and
press Ctrl+C to stop the server. Each run scores at most 25 jobs, so the backlog from a first run
takes a few days to clear.

If you have already been applying, read your older mail once:

```
python mail_sync.py --days 365 --limit 200
```

### 6. Load the extension

In Chrome, open `chrome://extensions`, turn on Developer mode, press "Load unpacked" and choose the
`extension` folder.

### 7. Schedule the daily run

In Windows Task Scheduler, create a task:

| Setting | Value |
|---|---|
| Trigger | Daily, at an hour your computer is usually on |
| Program | `conhost.exe` |
| Arguments | `--headless C:\path\to\job-agent\daily_run.cmd` |
| Settings | Tick "Run task as soon as possible after a scheduled start is missed" |

`conhost --headless` keeps a window from opening that could be closed mid-run. Output goes to
`logs\daily_run.log`.

## Day to day

The run has already happened, so most days start here:

```
python serve.py
```

**1. Read.** Open the jobs page. Remove what does not interest you, so it does not come back.

**2. Approve.** Read the drafts of the jobs you want. A job with no draft yet gets one from
*Prepare CV*. Tick the jobs you are happy with and press *Approve*; the Word and PDF files land
in `ready\`.

**3. Apply.** How depends on where the job lives:

| The job is on | What you do |
|---|---|
| Comeet | Press *Submit* on the card, or tick up to 10 and press *Submit ticked*. You see the destination and attachments, confirm, and it goes out from your Gmail. |
| Greenhouse, Workday, Lever, SmartRecruiters | Tick the jobs and press *Fill forms*. Each form opens filled in Chrome, with a panel listing what is left. You press every Next and Submit. The application is recorded when the confirmation page appears. |
| Anywhere else | Open the job's link. The extension shows that job's files in a panel. Apply by hand, then press *I applied*. |

An answer you correct on a form is remembered for later forms.

**4. Follow.** The tracking page shows each application by its latest news. Replies are read and
classified by themselves; a mail the classifier is unsure of waits for you to confirm or dismiss it.

**Now and then:**

- Change `profile.md` when your search changes. The next run scores jobs against the new version.
- Add to `facts.md` when you have something new to say.
- Look at the sources page for boards that stopped answering.

### The buttons

The pages are in Hebrew. These are the names used above:

| Name above | On the page |
|---|---|
| Jobs page | משרות |
| Tracking page | מעקב הגשות |
| Sources page | סריקת משרות |
| Prepare CV | הכן קורות חיים מותאמים |
| Approve | אשר והפק קבצים להגשה |
| Submit | הגש |
| Submit ticked | הגש את המסומנות |
| Fill forms | פתח ומלא טפסים בכרום |
| I applied | הגשתי |
| Remove | לא מעניין, הסר |
| Confirm a mail | נכון, רשום |
| Dismiss a mail | לא קשור להגשה |

Every command and its options, and what to do when something breaks, are in
[COMMANDS.md](COMMANDS.md).

## Where the jobs come from

The company list held 626 boards in November 2026, and the daily run keeps adding to it. A list you
build yourself will differ.

| Source | Boards | Used for |
|---|---|---|
| Comeet | 292 | Each company's public feed |
| Greenhouse | 135 | Job-board API |
| Workday | 112 | Careers sites with no documented API |
| Ashby | 43 | Job-board API |
| Lever | 21 | Job-board API |
| SmartRecruiters | 7 | Job-board API |
| Workable | 1 | Job-board API |
| Oracle, Eightfold, Bob | 3, 3, 2 | Careers sites with no documented API |
| Amazon, Google, Apple, Elbit | 1 each | Careers sites with no documented API |
| [DevJobs](https://devjobs.co.il) | 2 | A few companies whose own site cannot be read, and full posting text for others |
| [Israeli Tech Map](https://github.com/mluggy/techmap), by Michael Lugassy, ODbL 1.0 | 1 | Which companies are hiring, and the jobs of companies no board reads |
| LinkedIn | Not in the list | The public job search and job pages, read without an account, for jobs no other source lists |
| Common Crawl | Not in the list | Finding Comeet career pages |

Careers sites with no documented API give the data their own pages show, and can change without
notice.

Sites behind a JavaScript or CAPTCHA challenge are left alone; their jobs come from the Tech Map's
rows or from DevJobs when they list them.

## Why it is built this way

- **Rules before the model.** Plain rules drop most of the jobs, so the model only sees the rest, and
  only once each.
- **Companies are found by who is hiring.** A company the Tech Map lists as hiring in your fields
  that no board reads has its careers page searched for a known hiring system.
- **No score from a title alone.** A job whose full text cannot be found is listed but not scored.
- **The model chooses, it does not write.** A CV is a selection of fact ids printed in a fixed
  layout, and the letter is fixed lines you wrote. Two model-written letter lines were tried and
  dropped, because they read worse than fixed ones.
- **The check repeats before anything is sent**, because a model check is not perfectly consistent:
  a document that passed once can fail the same check again.
- **Forms are filled in your own Chrome, and you press the buttons.**
  [extension/README.md](extension/README.md) explains the extension, and
  [extension/THIRD_PARTY.md](extension/THIRD_PARTY.md) lists the code it adapts.
- **An application's stage is derived.** It is the latest event in an append-only log, so a wrong
  update is corrected by adding the right one.
- **Rules were measured.** Scoring was tuned against hand-labelled jobs, and filter rules were checked
  against the scores already collected.
- **Personal data stays out of the public code.** The public copy is built from an explicit list of
  files, and the build fails if a private value appears in it.

## Where things are

| Part | Files |
|---|---|
| The daily run | `run.py`, `daily_run.cmd` |
| Collect | `fetch_jobs.py`, `workday.py`, `smartrecruiters.py`, `employers.py` |
| Find companies | `catalogue.py`, `comeet.py`, `techmap.py`, `scout.py`, `devjobs.py`, `linkedin.py` |
| Filter and score | `filter_jobs.py`, `score_job.py`, `store.py` |
| Tailor and render | `tailor.py`, `cv_layout.py`, `keywords.py`, `render_docx.py` |
| Pages | `serve.py`, `report.py`, `browser.js`, `sources.py`, `tracking.py` |
| Apply | `approve.py`, `forms.py`, `submit.py`, `extension/` |
| Mail | `mail_sync.py` |
| Evaluate | `make_evalset.py`, `rate_jobs.py`, `rate_shortlist.py`, `calibrate.py`, `filter_review.py` |

| Folder | Holds |
|---|---|
| `applications\` | Drafts |
| `ready\` | Approved Word and PDF files |
| `sent\` | A copy of what was sent |
| `logs\` | The daily run's log |

## Limits

- Windows only in practice: PDF export drives the installed Microsoft Word.
- Only Comeet positions can be sent from the page. Forms on Greenhouse, Workday, Lever and
  SmartRecruiters are filled by the extension and sent by you; other systems are filled by hand.
- Jobs known only from the Tech Map or LinkedIn's search wait unscored until their full text is found.
- LinkedIn's public job page gives a job's text but not where to apply outside LinkedIn.
- Tests cover only the readers of other sites (`.venv\Scripts\python -m unittest discover tests`), on
  short pages written to their shape. The evaluation sets measure the scoring.
