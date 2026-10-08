# job-agent forms, the Chrome extension

Fills Greenhouse application forms from the local job-agent server, in the candidate's own
Chrome. It never presses submit: the candidate reads each form and sends it.

## How it works

1. The report's "open and fill forms" button asks `serve.py` to open the selected jobs'
   forms in Chrome, after checking each job is still open and has approved files.
2. On each form, `content.js` asks the server (through `background.js`, the only part that
   talks to 127.0.0.1) for the job's fill plan: every field's id, value and source, built by
   `forms.py` from the form's public question list and `answers.md`.
3. Text fields and file uploads are filled from the extension's world. Dropdowns are picked
   by `page.js`, which runs in the page's own world, because only there is the dropdown
   component's `selectOption` function visible.
4. A panel lists what was filled and what is left for the candidate: open questions, a
   question no rule can answer honestly, and any privacy acknowledgement, which is never
   ticked for them.
5. When the thank-you page appears, the answers the form held at submit are recorded with
   the application, and general answers are remembered for the next form.

## Workday

`workday.js` runs on Workday application pages. Workday publishes no question list and moves
through several steps on one page, so each step's fields are read off the page by their labels
and answered by the server (`/api/form/answers`, `forms.answer_questions`). It uploads the approved
CV where the page asks for one, so Workday's own resume parsing fills experience and education,
leaves the multi-select prompt, consents and anything unanswered for the candidate, and never
presses Save and Continue or Submit. The candidate signs in to each company's Workday first. It
has not been run against a live Workday form yet: forms sit behind a per-company sign-in.

## Install, once

1. Open `chrome://extensions` and turn on Developer mode, top right.
2. Press "Load unpacked" and choose this `extension` folder.
3. After any change to these files, press the reload icon on the extension's card.

`serve.py` must be running on its default port, 8777.

The extension's toolbar button turns it on and off; "OFF" on the icon means it fills, shows and
records nothing. Pin it from the puzzle-piece menu to keep the button in sight.

## Why an extension and not Playwright

A browser driven by Playwright announces itself as automated (`navigator.webdriver`), and
Greenhouse's invisible reCAPTCHA scores that browser lower, which can add an email
verification step. The extension runs in the candidate's ordinary browser, like any
autofill tool, so the form sees a person using a browser, which is what is happening.

`fillers.js` is adapted from avid-autofill under the MIT license; see `THIRD_PARTY.md`.
