// Runs on Workday application pages. Workday publishes no question list and walks through several
// steps on one page, so each step's fields are read off the page, answered by the local server
// from their labels, and filled; the candidate reviews each step and presses its own button.
// It never presses Save and Continue or Submit. Selectors follow avid-autofill's tested ones
// (MIT, see THIRD_PARTY.md): Workday marks every field with data-automation-id.
(function () {
  if (!/myworkdayjobs\.com$/.test(location.hostname)) return;
  const F = JobAgent.fillers;
  const DONE = /application (was |has been )?(successfully )?submitted|thank you for applying|we have received your application/i;
  const FIELD = '[data-automation-id^="formField-"]';

  function ask(message) {
    return new Promise((resolve) =>
      chrome.runtime.sendMessage(message, (answer) => resolve(answer || { error: "התוסף לא ענה" }))
    );
  }

  function jobKey() {
    const match = location.pathname.match(/\/job\/[^/]+\/([^/?#]+)/);
    return match && match[1];
  }

  const visible = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const clean = (text) => String(text || "").replace(/\*/g, "").replace(/\bRequired\b/i, "").replace(/\s+/g, " ").trim();

  function labelOf(el) {
    const wrap = el.closest(FIELD) || el.closest("fieldset");
    const own = wrap && (wrap.querySelector("label") || wrap.querySelector("legend"));
    if (own && clean(own.innerText)) return clean(own.innerText);
    const by = el.getAttribute("aria-labelledby");
    const text = by && by.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.innerText).join(" ");
    return clean(text || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "");
  }

  // Every field the current step shows, as a question the server can answer by its label.
  function readStep() {
    const fields = [];
    const seen = new Set();
    let n = 0;
    const add = (el, kind, extra = {}) => {
      const label = labelOf(el);
      if (!label || seen.has(el)) return;
      seen.add(el);
      fields.push({ key: `f${n++}`, el, label, kind, ...extra });
    };
    for (const el of document.querySelectorAll(`${FIELD} input[type="text"], ${FIELD} input[type="email"], ${FIELD} input[type="tel"], ${FIELD} textarea`)) {
      if (!visible(el) || el.readOnly || el.disabled) continue;
      if (/searchBox|dateSection/i.test(el.getAttribute("data-automation-id") || "")) continue;
      if (el.closest('[data-automation-id="multiSelectContainer"]')) continue;
      add(el, el.tagName === "TEXTAREA" ? "textarea" : "text");
    }
    for (const el of document.querySelectorAll('button[aria-haspopup="listbox"]')) {
      if (visible(el)) add(el, "choice");
    }
    const groups = {};
    for (const radio of document.querySelectorAll('input[type="radio"]')) {
      if (radio.name && visible(radio.closest("label") || radio)) (groups[radio.name] = groups[radio.name] || []).push(radio);
    }
    for (const radios of Object.values(groups)) {
      const holder = radios[0].closest("fieldset") || radios[0].closest(FIELD) || radios[0];
      add(holder, "choice", { radios, options: radios.map((r) => clean((r.labels && r.labels[0] && r.labels[0].innerText) || r.value)) });
    }
    // Workday's multi-select prompt resists scripted input, so it is listed for the candidate.
    for (const el of document.querySelectorAll('[data-automation-id="multiSelectContainer"]')) {
      if (visible(el)) add(el, "multiselect");
    }
    for (const el of document.querySelectorAll(`${FIELD} input[type="checkbox"]`)) {
      if (visible(el)) add(el, "checkbox");
    }
    return fields;
  }

  function current(field) {
    if (field.kind === "choice" && field.radios) {
      const on = field.radios.find((r) => r.checked);
      return on ? clean((on.labels && on.labels[0] && on.labels[0].innerText) || on.value) : "";
    }
    if (field.kind === "choice") return /^select one$/i.test(clean(field.el.innerText)) ? "" : clean(field.el.innerText);
    if (field.kind === "checkbox") return field.el.checked ? "Yes" : "";
    if (field.kind === "multiselect") {
      // The chosen items, not the container's text, which also says "1 item selected" and repeats each one.
      const box = field.el.closest(FIELD) || field.el;
      const items = Array.from(box.querySelectorAll('[data-automation-id="selectedItem"]')).map((p) => clean(p.innerText));
      return [...new Set(items.filter(Boolean))].join(", ");
    }
    return field.el.value || "";
  }

  const matches = (text, wanted) => {
    const a = F.normalize(text), b = F.normalize(wanted);
    return a === b || a.startsWith(b) || (b.length > 2 && a.includes(b));
  };

  // Open a Workday dropdown, then click the first option that matches any acceptable answer in order.
  // A value already chosen, shown in a field's list of selected items, carries the same marks as an option
  // of an open list, so it is left out; otherwise it reads as the list having arrived, or as a result.
  const openOptions = () => Array.from(document.querySelectorAll('[data-automation-id="promptOption"], [role="option"]'))
    .filter(visible)
    .filter((o) => !o.closest('[data-automation-id="selectedItemList"], [data-automation-id="selectedItem"]'));

  // Open a Workday dropdown, wait for its options to stop arriving, then click the first acceptable answer.
  async function choose(button, candidates) {
    button.click();
    let options = [];
    let last = -1;
    for (let i = 0; i < 30; i++) {
      await F.sleep(200);
      options = openOptions();
      if (options.length && options.length === last) break;
      last = options.length;
    }
    for (const wanted of candidates) {
      const option = options.find((o) => matches(o.innerText, wanted));
      if (option) {
        option.click();
        await F.sleep(300);
        return true;
      }
    }
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return false;
  }

  const attached = {};

  // Experience and education are built entry by entry: each section has panels named like
  // "Work-Experience-1-panel" and its own "Add" button, and each panel holds the same fields.
  const panels = (section) => Array.from(document.querySelectorAll(`[role="group"][aria-labelledby^="${section}-"]`))
    .filter((g) => /-\d+-panel$/.test(g.getAttribute("aria-labelledby")));
  const inside = (panel, name, what = "input") => panel.querySelector(`[data-automation-id="formField-${name}"] ${what}`);

  async function panelAt(section, index) {
    if (panels(section).length > index) return panels(section)[index];
    const head = document.querySelector(`[role="group"][aria-labelledby="${section}-section"]`);
    const add = head && head.querySelector('[data-automation-id="add-button"]');
    if (!add) return null;
    add.click();
    for (let i = 0; i < 20 && panels(section).length <= index; i++) await F.sleep(200);
    return panels(section)[index] || null;
  }

  // A field already holding something is left alone, so a value the candidate typed is never replaced.
  function text(panel, name, value, what = "input") {
    const el = inside(panel, name, what);
    if (!el || !value || el.value) return false;
    F.setTextValue(el, value);
    return true;
  }

  // A Workday date is one widget with a month part and a year part. It takes the date only as typed,
  // one digit at a time from the month on, moving to the year by itself; a value set into each part
  // shows on screen but leaves the widget's own date empty, which Workday then rejects as invalid.
  async function monthYear(panel, name, month, year) {
    const field = panel.querySelector(`[data-automation-id="formField-${name}"]`);
    const m = field && field.querySelector('[data-automation-id="dateSectionMonth-input"]');
    const y = field && field.querySelector('[data-automation-id="dateSectionYear-input"]');
    if (!y || !year || (m && !month)) return false;
    const typed = m ? m.value && y.value : y.value;
    if (typed && !/invalid date/i.test(field.innerText)) return false;
    const first = m || y;
    first.click();
    first.focus();
    if (first.select) first.select();
    await F.sleep(150);
    for (const digit of (m ? String(month).padStart(2, "0") : "") + String(year)) {
      const el = document.activeElement;
      const code = 48 + Number(digit);
      el.dispatchEvent(new KeyboardEvent("keydown", { key: digit, code: "Digit" + digit, keyCode: code, which: code, bubbles: true, cancelable: true }));
      document.execCommand("insertText", false, digit);
      el.dispatchEvent(new KeyboardEvent("keyup", { key: digit, code: "Digit" + digit, keyCode: code, which: code, bubbles: true }));
      await F.sleep(90);
    }
    document.activeElement.blur();
    await F.sleep(200);
    return true;
  }

  // A searchable list runs Workday's search on Enter. The term goes in the way a text field is filled,
  // which also leaves and re-enters the box, and that is what makes Enter run the search; the first
  // live run kept 12 of 17 skills this way. A strict search takes only the term itself or the term
  // followed by a bracket, so "Java" picks "Java (Programming Language)" and never "JavaFX".
  async function search(field, candidates, strict = false) {
    const input = field && field.querySelector("input");
    if (!input) return false;
    const exact = (o, wanted) => {
      const b = F.normalize(wanted);
      return F.normalize(o.innerText) === b || F.normalize(o.innerText.replace(/\s*\(.*\)\s*$/, "")) === b;
    };
    for (const wanted of candidates) {
      input.focus();
      F.setTextValue(input, wanted);
      input.focus();
      for (const type of ["keydown", "keypress", "keyup"]) {
        input.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
      }
      let option = null;
      for (let i = 0; i < 16 && !option; i++) {
        await F.sleep(250);
        // Only the option itself takes a click; the list item wrapped around it shows the same text
        // but ignores one, so a click there leaves nothing selected.
        const options = openOptions().filter((o) => o.getAttribute("data-automation-id") === "promptOption");
        option = options.find((o) => exact(o, wanted)) || (strict ? null : options.find((o) => matches(o.innerText, wanted)) ||
          options.find((o) => wanted.split(/\s+/).every((w) => F.normalize(o.innerText).includes(F.normalize(w)))));
      }
      if (option) {
        option.click();
        await F.sleep(500);
        return true;
      }
    }
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return false;
  }

  async function fillLanguages(languages) {
    const done = [];
    if (!document.querySelector('[role="group"][aria-labelledby="Languages-section"]')) return done;
    for (let i = 0; i < languages.length; i++) {
      const panel = await panelAt("Languages", i);
      if (!panel) break;
      const lang = languages[i];
      const pick = panel.querySelector('[data-automation-id="formField-language"] button');
      if (pick && /select one/i.test(pick.innerText) && await choose(pick, [lang.name])) done.push(lang.name);
      const fluent = panel.querySelector('[data-automation-id="formField-native"] input[type="checkbox"]');
      if (fluent && lang.native && !fluent.checked) fluent.click();
      // Every other list in the entry is a proficiency, named differently by each company.
      for (const field of panel.querySelectorAll('[data-automation-id^="formField-"]')) {
        if (field.getAttribute("data-automation-id") === "formField-language") continue;
        const button = field.querySelector("button");
        const label = (field.querySelector("label, legend") || {}).innerText || "";
        // A level that covers speaking takes the spoken one, which for English sits a step lower.
        const levels = /speak|spoken|oral|conversation/i.test(label) && lang.spoken ? lang.spoken : lang.levels;
        if (button && /select one/i.test(button.innerText)) await choose(button, levels);
      }
    }
    return done;
  }

  async function fillSkills(skills) {
    const field = document.querySelector('[data-automation-id="formField-skills"]');
    if (!field || !skills.length) return [];
    const have = Array.from(field.querySelectorAll('[data-automation-id^="selectedItem"]')).map((p) => F.normalize(p.innerText));
    const added = [];
    for (const skill of skills) {
      if (have.some((h) => h === F.normalize(skill) || h.startsWith(F.normalize(skill) + " "))) continue;
      if (await search(field, [skill], true)) added.push(skill);
    }
    return added;
  }

  async function fillExperience(experience) {
    const filled = [];
    if (!experience || !document.querySelector('[role="group"][aria-labelledby$="-section"]')) return filled;
    const work = experience.work || [];
    if (document.querySelector('[role="group"][aria-labelledby="Work-Experience-section"]')) {
      for (let i = 0; i < work.length; i++) {
        const panel = await panelAt("Work-Experience", i);
        if (!panel) break;
        const w = work[i];
        if (text(panel, "jobTitle", w.title)) filled.push(`${w.title}, ${w.company}`);
        text(panel, "companyName", w.company);
        text(panel, "location", w.location || "Israel");
        await monthYear(panel, "startDate", w.from_month, w.from_year);
        await monthYear(panel, "endDate", w.to_month, w.to_year);
        text(panel, "roleDescription", w.description, "textarea");
      }
    }
    const education = experience.education || [];
    if (document.querySelector('[role="group"][aria-labelledby="Education-section"]')) {
      for (let i = 0; i < education.length; i++) {
        const panel = await panelAt("Education", i);
        if (!panel) break;
        const e = education[i];
        if (text(panel, "schoolName", e.school)) filled.push(e.school);
        const degree = panel.querySelector('[data-automation-id="formField-degree"] button');
        if (degree && /select one/i.test(degree.innerText)) {
          await choose(degree, ["Bachelor of Science", e.degree, "B.Sc", "B.S.", "BSc", "Bachelor's", "Bachelor"]);
        }
        const study = panel.querySelector('[data-automation-id="formField-fieldOfStudy"]');
        if (study && !study.querySelector('[data-automation-id^="selectedItem"]')) {
          await search(study, [e.field, "Computer and Information Science", "Computer"]);
        }
        text(panel, "gradeAverage", experience.gpa);
        await monthYear(panel, "firstYearAttended", e.from_month, e.from_year);
        await monthYear(panel, "lastYearAttended", e.to_month, e.to_year);
      }
    }
    for (const name of await fillLanguages(experience.languages || [])) filled.push(name);
    const skills = await fillSkills(experience.skills || []);
    if (skills.length) filled.push(`${skills.length} skills`);
    return filled;
  }

  async function uploadResume(plan) {
    const input = document.querySelector('input[type="file"][data-automation-id="file-upload-input-ref"], input[type="file"]');
    const info = plan.files && plan.files.resume;
    // Workday keeps a file uploaded earlier through a reload, so a file already listed is not sent again.
    const listed = document.querySelector('[data-automation-id="file-upload-item"]');
    if (!input || !info || !info.ok || attached.resume || listed) return;
    const answer = await ask({ type: "file", path: info.path });
    if (answer.error) return;
    const bytes = Uint8Array.from(atob(answer.base64), (c) => c.charCodeAt(0));
    const file = new File([bytes], info.name, { type: "application/pdf" });
    F.uploadToInput(input, file);
    attached.resume = file;
  }

  // Fill one step: ask for answers by label, fill what is empty, and say what is left.
  async function fillStep() {
    const fields = readStep();
    const questions = fields.map(({ key, label, kind, options }) => ({ key, label, kind, options }));
    // Asked even with no questions, because the reply also carries the job's approved files.
    const reply = await ask({ type: "answers", url: location.href, questions });
    if (reply.error) return { error: reply.error };
    plan = { ...plan, ...reply };
    await uploadResume(plan);
    // The experience entries first, so their fields are full before the generic pass looks for empty ones.
    const entries = await fillExperience(reply.experience);
    const byKey = Object.fromEntries((reply.answers || []).map((a) => [a.key, a]));
    const outcome = { filled: entries.map((label) => ({ label })), waiting: [] };
    for (const field of fields) {
      const answer = byKey[field.key] || {};
      field.source = answer.source || "none";
      field.value = answer.value;
      const candidates = Array.isArray(answer.value) ? answer.value : answer.value ? [String(answer.value)] : [];
      const now = current(field);
      // Workday fills some fields itself, such as the address from the account. A value the candidate
      // gave before, or a personal detail from answers.md, replaces it; any other answer fills only an
      // empty field, so what Workday or the candidate put there is otherwise left alone.
      const ours = answer.source === "memory" || answer.source === "identity";
      if (now && !(ours && candidates.length && !candidates.some((c) => matches(now, c)))) continue;
      let ok = false;
      try {
        if (!candidates.length || field.kind === "checkbox") ok = false;
        else if (field.kind === "multiselect") ok = await search(field.el.closest(FIELD) || field.el, candidates);
        else if (field.kind === "choice" && field.radios) {
          const radio = field.radios.find((r, i) => candidates.some((c) => matches(field.options[i], c)));
          if (radio) (radio.click(), (ok = true));
        } else if (field.kind === "choice") ok = await choose(field.el, candidates);
        else (F.setTextValue(field.el, String(candidates[0])), (ok = true));
      } catch (_) {
        ok = false;
      }
      (ok && current(field) ? outcome.filled : outcome.waiting).push(field);
    }
    // Kept across steps for the record; a field read again on "fill again" replaces its earlier entry.
    for (const field of fields) {
      const at = steps.findIndex((s) => s.el === field.el);
      if (at >= 0) steps[at] = field;
      else steps.push(field);
    }
    return outcome;
  }

  // A small panel in its own shadow root, re-attached if the page's own rendering drops it.
  const host = document.createElement("div");
  host.id = "job-agent-panel";
  const root = host.attachShadow({ mode: "open" });
  // The folder button opens the job's approved files, named for a recruiter, in the file explorer.
  root.addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest("[data-folder]")) return;
    ask({ type: "folder", job_id: plan.job_id }).then((a) => {
      if (a && a.error) e.target.textContent = a.error;
    });
  });
  const escape = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  function show(html, button) {
    if (!host.isConnected) document.documentElement.appendChild(host);
    root.innerHTML = `<style>
      .box{position:fixed;left:12px;bottom:12px;z-index:2147483647;width:260px;max-height:40vh;overflow-y:auto;overflow-x:hidden;
        background:#fff;color:#18232b;border:1px solid #c9d3d8;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.15);
        font:14px/1.5 "Segoe UI",Arial,sans-serif;direction:rtl;padding:12px 14px}
      h1{font-size:15px;margin:0 0 6px} ul{margin:4px 0 8px;padding:0;list-style:none} li{direction:ltr;text-align:right;unicode-bidi:isolate} li::after{content:" •";color:#b0621a} .folder{font:inherit;font-size:13px;border:0;background:none;color:#1d6b5a;text-decoration:underline;cursor:pointer;padding:0;margin:2px 0}
      .muted{color:#5a6a74} .warn{color:#b0621a} a{color:#1d6b5a}
      button{font:inherit;border:1px solid #c9d3d8;background:#f3f6f7;border-radius:6px;padding:4px 10px;cursor:pointer;margin-top:8px}
    </style><div class="box" role="status">${html}</div>`;
    if (button) {
      const b = document.createElement("button");
      b.textContent = button.label;
      b.onclick = button.onclick;
      root.querySelector(".box").appendChild(b);
    }
  }

  let plan = {};
  const steps = [];
  let active = false;
  let wanted = false;

  async function runStep() {
    if (!active || !wanted) return;
    show(`<h1>job-agent</h1><div class="muted">ממלא את השלב הזה...</div>`);
    const outcome = await fillStep();
    if (outcome.error) return show(`<h1>job-agent</h1><div class="warn">${escape(outcome.error)}</div>`);
    const list = (items) => `<ul>${items.map((f) => `<li>${escape(f.label)}</li>`).join("")}</ul>`;
    // The folder button shows whether or not a CV was attached here, since the files may be wanted anyway.
    const files = (attached.resume ? `<div><a href="${URL.createObjectURL(attached.resume)}" target="_blank" rel="noopener">פתח את קורות החיים שצורפו</a></div>` : "") + `<div><button class="folder" data-folder>פתח את תיקיית הקבצים</button></div>`;
    show(`<h1>job-agent</h1><div class="muted">${escape(plan.company || "")}</div>
      <div>מולאו ${outcome.filled.length} שדות בשלב הזה.</div>${files}
      ${outcome.waiting.length ? `<div class="warn">מחכים לך (${outcome.waiting.length}):</div>${list(outcome.waiting)}` : ""}
      <div class="muted">עבור על השלב ולחץ בעצמך על הכפתור של Workday כדי להמשיך.</div>`,
      { label: "מלא שוב", onclick: runStep });
  }

  let uploading = false;
  async function autofillUpload() {
    if (uploading) return;
    uploading = true;
    show(`<h1>job-agent</h1><div class="muted">מצרף את קורות החיים המותאמים...</div>`);
    const reply = await ask({ type: "answers", url: location.href, questions: [] });
    if (reply.error) {
      uploading = false;
      return show(`<h1>job-agent</h1><div class="warn">${escape(reply.error)}</div>`);
    }
    plan = { ...plan, ...reply };
    await uploadResume(plan);
    uploading = false;
    show(attached.resume
      ? `<h1>job-agent</h1><div>צירפתי את <span dir="ltr">${escape(attached.resume.name)}</span>.</div>
         <div><a href="${URL.createObjectURL(attached.resume)}" target="_blank" rel="noopener">פתח את הקובץ שצורף</a></div>
         <div class="muted">ודא שזה הקובץ ולחץ בעצמך על הכפתור של Workday כדי להמשיך.</div>`
      : `<h1>job-agent</h1><div class="warn">לא מצאתי קורות חיים מאושרים למשרה הזו. אשר אותם בדף המשרות.</div>`);
  }

  // What each step held when its own button was pressed, kept until the confirmation proves it was sent.
  function snapshot() {
    const key = jobKey();
    if (!active || !wanted || !key || !plan.job_id) return;
    // The step is read again as it stands: Workday rebuilds some fields, such as the address after a
    // country is chosen, so the field read when the step was filled may be gone, and a field the
    // candidate filled alone was never read at all. Each one is matched by its label.
    for (const fresh of readStep()) {
      const known = steps.find((f) => f.el === fresh.el) ||
        steps.find((f) => !f.el.isConnected && f.label === fresh.label);
      if (known) known.el = fresh.el;
      else steps.push({ ...fresh, source: "none", value: null });
    }
    const values = steps.filter((f) => f.el.isConnected || f.captured).map((f) => {
      if (f.el.isConnected) f.captured = current(f);
      const want = Array.isArray(f.value) ? f.value : f.value ? [String(f.value)] : [];
      // A field inside an experience, education or language entry, or the skills, comes from the facts;
      // it is recorded with the application but marked so it is never remembered as a general answer.
      const entry = !!f.el.closest('[aria-labelledby$="-panel"]') || !!f.el.closest('[data-automation-id="formField-skills"]');
      return { id: f.key, label: f.label, kind: entry ? "entry" : f.kind === "choice" ? "select" : f.kind, source: f.source,
               value: f.captured, changed: !!f.captured && !want.some((w) => matches(f.captured, w)) };
    });
    ask({ type: "pending", number: key, snapshot: { job_id: plan.job_id, values } });
    ask({ type: "remember", job_id: plan.job_id, values });
  }

  function quiet(stillFor, atMost) {
    return new Promise((resolve) => {
      let timer = setTimeout(done, stillFor);
      const cap = setTimeout(done, atMost);
      const watch = new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(done, stillFor);
      });
      watch.observe(document.body, { childList: true, subtree: true });
      function done() {
        watch.disconnect();
        clearTimeout(timer);
        clearTimeout(cap);
        resolve();
      }
    });
  }

  // A new step replaces the page's content without loading a page, so watch for its heading to change.
  let lastStep = "";
  let timer = null;
  let reported = false;
  // Pressing Submit leaves a note in this tab: which job, and when. Workday either shows a thank-you
  // page or goes straight to the candidate's home page, where the job is listed under My Applications,
  // and neither keeps the job in the address; the note says which job the success belongs to.
  const SUBMIT_NOTE = "job-agent-submit";
  const NOTE_MINUTES = 20;
  function readNote() {
    try {
      const note = JSON.parse(sessionStorage.getItem(SUBMIT_NOTE) || "null");
      return note && Date.now() - note.at < NOTE_MINUTES * 60000 ? note : null;
    } catch (_) {
      return null;
    }
  }

  function submittedNow(note) {
    const text = document.body.innerText;
    if (DONE.test(text) && !document.querySelector(FIELD)) return true;
    // The home page lists each application with its requisition number, which ends the job's address.
    return /\/userHome/.test(location.pathname) && !!note.req && text.includes(note.req);
  }

  function check() {
    const note = readNote();
    if (!reported && note && submittedNow(note)) {
      reported = true;
      try { sessionStorage.removeItem(SUBMIT_NOTE); } catch (_) {}
      if (active) ask({ type: "submitted", number: note.key }).then((a) =>
        a && a.application && show(`<h1>job-agent</h1><div>ההגשה נרשמה במעקב.</div>`));
      return;
    }
    // "Autofill with Resume" opens on an upload box alone, before any step; the tailored CV goes there,
    // and Workday reads it into the steps that follow.
    if (active && wanted && !attached.resume && !document.querySelector(FIELD) && document.querySelector('input[type="file"]')
        && !document.querySelector('[data-automation-id="file-upload-item"]')) {
      autofillUpload();
      return;
    }
    // The progress bar names the step; a selector list would return the job's own title first, which
    // sits higher on the page and never changes, so the progress bar is asked for on its own.
    const heading = document.querySelector('[data-automation-id="progressBarActiveStep"]') ||
      document.querySelector("main h2, h2");
    const name = heading ? heading.innerText.trim() : "";
    if (name && name !== lastStep && document.querySelector(FIELD)) {
      lastStep = name;
      // A step draws its sections in turns, so the fill waits until the page has been still for a moment.
      quiet(1500, 10000).then(runStep);
    }
  }
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(check, 900);
  });

  document.addEventListener("click", (e) => {
    const button = e.target.closest("button");
    const footer = button && /bottom-navigation-next-button|pageFooterNextButton/.test(button.getAttribute("data-automation-id") || "");
    if (!button || !(footer || /^(save and continue|next|submit|review)$/i.test(clean(button.innerText)))) return;
    snapshot();
    const key = jobKey();
    if (active && wanted && key && /^submit$/i.test(clean(button.innerText))) {
      const req = (key.match(/_([A-Za-z]*-?\d+(?:-\d+)?)$/) || [])[1] || "";
      try { sessionStorage.setItem(SUBMIT_NOTE, JSON.stringify({ key, req, at: Date.now() })); } catch (_) {}
    }
  }, true);

  // Only an application opened by the report's button fills on its own; the mark in its address is
  // remembered for this tab, because signing in to Workday moves through pages that drop it.
  function start() {
    const key = jobKey();
    // A page without the job in its address is watched only while a submit note waits for its answer.
    if (!key) {
      if (readNote()) {
        observer.observe(document.body, { childList: true, subtree: true });
        setTimeout(check, 1500);
      }
      return;
    }
    const flag = `job-agent-fill:${key}`;
    wanted = location.hash.includes("job-agent-fill");
    try {
      if (wanted) sessionStorage.setItem(flag, "1");
      else wanted = sessionStorage.getItem(flag) === "1";
    } catch (_) {}
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(check, 1500); // a step already on the page when the script starts
    if (!wanted && /\/apply/.test(location.pathname)) {
      show(`<h1>job-agent</h1><div class="muted">הטופס לא נפתח מדף המשרות, ולכן לא מולא.</div>`, {
        label: "מלא את הטופס",
        onclick: () => {
          wanted = true;
          try { sessionStorage.setItem(flag, "1"); } catch (_) {}
          lastStep = "";
          runStep();
        },
      });
    }
  }

  chrome.storage.local.get("enabled").then(({ enabled = true }) => {
    active = enabled;
    if (active) start();
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.enabled) return;
    active = changes.enabled.newValue !== false;
    if (!active) host.remove();
  });
})();
