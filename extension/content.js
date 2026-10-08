// Runs inside a Greenhouse application page: asks the local server for this job's plan, fills the
// form, shows what is left for the candidate, and reports the application once it is confirmed.
// It never presses submit. The candidate reads the form and sends it.
(function () {
  const F = JobAgent.fillers;
  const CONFIRMED = /thank you for applying|application (has been )?(received|submitted)|we('ve| have) received your application/i;
  const FORM = "#application-form, #application_form, form[id*='application']";

  function ask(message) {
    return new Promise((resolve) =>
      chrome.runtime.sendMessage(message, (answer) => resolve(answer || { error: "התוסף לא ענה" }))
    );
  }

  async function waitFor(test, ms) {
    for (const end = Date.now() + ms; Date.now() < end; await F.sleep(300)) {
      const found = test();
      if (found) return found;
    }
    return null;
  }

  function jobNumber() {
    const match = location.href.match(/(?:gh_jid=|\/jobs\/|[?&]token=)(\d{5,})/);
    return match && match[1];
  }

  function confirmed() {
    return /\/confirmation/.test(location.pathname) || (!document.querySelector(FORM) && CONFIRMED.test(document.body.innerText));
  }

  // A multi-choice question can be drawn as checkboxes, one per option, with ids "<question>[]_<option>".
  function checkboxes(entry) {
    return Array.from(document.querySelectorAll(`input[type="checkbox"][id^="${CSS.escape(entry.id + "[]_")}"]`));
  }

  function labelOf(input) {
    return input.labels && input.labels[0] ? input.labels[0].innerText.trim() : "";
  }

  function field(entry) {
    if (entry.kind === "location") return document.getElementById("candidate-location") || document.getElementById(entry.id);
    return document.getElementById(entry.id) || checkboxes(entry)[0] || null;
  }

  function isEmpty(value) {
    return value == null || value === "" || (Array.isArray(value) && value.length === 0);
  }

  // What a control shows now, read the way a person would see it, so the record matches the form.
  function read(entry) {
    const el = field(entry);
    if (!el) return null;
    if (el.type === "checkbox") return checkboxes(entry).filter((b) => b.checked).map(labelOf);
    if (entry.kind === "file") return el.files && el.files[0] ? el.files[0].name : null;
    if (el.tagName === "SELECT") return el.selectedIndex > 0 ? el.options[el.selectedIndex].textContent.trim() : null;
    if (el.getAttribute("role") === "combobox") {
      // The chosen value is drawn inside the control, next to the input, not inside the input itself.
      const box = el.closest('[class*="control"]') || el.parentElement;
      if (entry.kind === "multiselect") {
        return Array.from(box.querySelectorAll('[class*="multi-value__label"], [class*="multiValue"] div:first-child'))
          .map((n) => n.textContent.trim()).filter(Boolean);
      }
      const shown = box.querySelector('[class*="single-value"], [class*="singleValue"]');
      return shown ? shown.textContent.trim() : null;
    }
    return el.value || null;
  }

  // Outline a control the candidate still has to deal with: orange for empty, blue for a model draft to read.
  function mark(entry, colour = "#d9822b") {
    const el = field(entry);
    if (!el) return;
    const box = el.closest('[class*="control"]') || el;
    box.style.outline = `2px solid ${colour}`;
    box.style.outlineOffset = "2px";
  }

  // Whether what the form shows is still what the plan put there, allowing for the form's own
  // formatting, so an answer is recorded as the candidate's only when they actually changed it.
  function same(entry, now) {
    const want = entry.value;
    const text = (v) => F.normalize(Array.isArray(v) ? v.join(", ") : v);
    if (isEmpty(want)) return isEmpty(now);
    if (entry.id === "country") return true; // the form sets it from the phone number
    if (entry.id === "phone") return String(now || "").replace(/\D/g, "") === String(want).replace(/\D/g, "");
    if (entry.kind === "education") return want.some((w) => F.normalize(w) === F.normalize(now));
    if (entry.kind === "location") return F.normalize(now).startsWith(F.normalize(want)); // "Haifa, Haifa District, Israel"
    return text(now) === text(want);
  }

  // Ask page.js, which runs in the page's world, and wait for its answer to this request.
  function pageCall(type, payload, ms) {
    const token = Math.random().toString(36).slice(2);
    return new Promise((resolve) => {
      const done = (event) => {
        let answer;
        try {
          answer = JSON.parse(event.detail);
        } catch (_) {
          return;
        }
        if (answer.token !== token) return;
        window.removeEventListener("job-agent-selected", done);
        resolve(answer);
      };
      window.addEventListener("job-agent-selected", done);
      window.dispatchEvent(new CustomEvent(type, { detail: JSON.stringify({ ...payload, token }) }));
      setTimeout(() => (window.removeEventListener("job-agent-selected", done), resolve({ ok: false })), ms);
    });
  }

  // Pick options through the dropdown's own API. An education field names the search to run
  // instead, and its values are candidates in order; the city runs the page's place search.
  async function selectInPage(id, values, search) {
    const type = search === "location" ? "job-agent-location" : search ? "job-agent-education" : "job-agent-select";
    return (await pageCall(type, { id, values, search }, search ? 8000 : 1500)).ok;
  }

  // The questions the screen shows as answered but the form's own record does not hold. That record
  // is what the form checks on submit, and a form was seen showing "Yes" and then reporting the
  // field as required. A single choice is picked again; anything still missing goes to the panel.
  async function unsaved(plan) {
    const shown = plan.fields.filter((e) => /^question_\d+$/.test(e.id) &&
      (e.kind === "select" || e.kind === "multiselect") && field(e) && !isEmpty(read(e)));
    if (!shown.length) return [];
    const answer = await pageCall("job-agent-state", { ids: shown.map((e) => e.id) }, 1500);
    return answer.ok && answer.values ? shown.filter((e) => isEmpty(answer.values[e.id])) : [];
  }

  async function settle(plan) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const missing = (await unsaved(plan)).filter((e) => e.kind === "select");
      if (!missing.length) break;
      for (const entry of missing) await selectInPage(field(entry).id, [read(entry)]);
      await F.sleep(600);
    }
    notSaved = await unsaved(plan);
  }
  let notSaved = [];

  function toFile(base64, name) {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    return new File([bytes], name, { type: "application/pdf" });
  }

  // Wait for the page's own code to finish starting. Anything typed before that is wiped when the
  // page takes over the form, and an upload fails with "reading 'uploadFile'" because the signed
  // upload address is fetched only then. That fetch ending is the sign the page is ready.
  // The page does not start at all while its tab is in the background, so a form opened in a
  // background tab is filled only once the candidate switches to it.
  async function pageReady() {
    if (document.visibilityState !== "visible") {
      await new Promise((resolve) => {
        const shown = () => document.visibilityState === "visible" &&
          (document.removeEventListener("visibilitychange", shown), resolve());
        document.addEventListener("visibilitychange", shown);
      });
    }
    const arrived = () =>
      performance.getEntriesByType("resource").some((e) => e.name.includes("presigned_fields") && e.responseEnd > 0);
    await waitFor(arrived, 15000);
    await F.sleep(700);
  }

  async function fill(plan, withFiles, reselect = false) {
    for (const entry of plan.fields) {
      if (!active) return; // turned off with the toolbar button
      const el = field(entry);
      if (!el || isEmpty(entry.value)) continue; // an uploaded file replaces its input, so a missing input is done
      try {
        if (entry.kind === "file") {
          const info = withFiles && plan.files[entry.value];
          if (!info) continue;
          const answer = await ask({ type: "file", path: info.path });
          if (!answer.error) {
            const file = toFile(answer.base64, info.name);
            F.uploadToInput(el, file);
            attached[entry.value] = file;
          }
          continue;
        }
        // Never overwrite what is already there, with one exception: a single-choice answer this plan
        // put there is chosen again, because a form was seen showing "Yes" while its own state was
        // empty and reported the field as required. Choosing the same option again sets both.
        const again = reselect && entry.kind === "select" && el.tagName !== "SELECT" && same(entry, read(entry));
        if (!isEmpty(read(entry)) && !again) continue;
        const values = Array.isArray(entry.value) ? entry.value : [String(entry.value)];
        if (entry.kind === "text" || entry.kind === "textarea") {
          F.setTextValue(el, String(entry.value));
        } else if (el.type === "checkbox") {
          for (const box of checkboxes(entry)) {
            const name = F.normalize(labelOf(box));
            if (!box.checked && values.some((v) => name === F.normalize(v))) box.click();
          }
        } else if (entry.kind === "education") {
          await selectInPage(el.id, values, entry.search);
        } else if (entry.kind === "location") {
          await selectInPage(el.id, values, "location");
        } else if (el.tagName === "SELECT") {
          F.setNativeSelect(el, entry.value);
        } else if (!(await selectInPage(el.id, values)) && values.length === 1) {
          // The dropdown's own API first; opening the menu by events is the fallback, and it only
          // works in the tab that has focus.
          await F.setReactSelect(el, values[0]);
        }
      } catch (_) {
        // judged below by what the form shows, like every other field
      }
    }
  }

  // Judge every field by what the form shows now, not by what was attempted, and outline the rest.
  function status(plan) {
    const outcome = { filled: [], waiting: [], failed: [], files: [], drafted: [], unsaved: [] };
    for (const entry of plan.fields) {
      const el = field(entry);
      if (entry.kind === "file") {
        if (!plan.files[entry.value]) {
          if (entry.required) outcome.waiting.push(entry);
        } else if (!el || (el.files && el.files.length)) outcome.files.push(entry);
        else outcome.failed.push(entry);
        continue;
      }
      if (!el) continue;
      const now = read(entry);
      if (isEmpty(entry.value)) {
        if (isEmpty(now) && (entry.required || entry.source === "consent")) outcome.waiting.push(entry);
      } else if (isEmpty(now)) outcome.failed.push(entry);
      else if (notSaved.includes(entry)) outcome.unsaved.push(entry);
      else (entry.source === "model" ? outcome.drafted : outcome.filled).push(entry);
    }
    outcome.unsaved.forEach((e) => mark(e, "#c0392b"));
    [...outcome.waiting, ...outcome.failed].forEach((e) => mark(e));
    outcome.drafted.forEach((e) => mark(e, e.flag ? "#c0392b" : "#2f6fd6"));
    return outcome;
  }

  // The job this tab fills, for the folder button once the plan has named it.
  let currentJob = null;

  // A small panel in its own shadow root, so the page's styles and ours never mix.
  function makePanel() {
    const host = document.createElement("div");
    host.id = "job-agent-panel";
    document.documentElement.appendChild(host);
    const root = host.attachShadow({ mode: "open" });
    root.addEventListener("click", (e) => {
      if (!e.target.closest || !e.target.closest("[data-folder]")) return;
      ask({ type: "folder", job_id: currentJob }).then((a) => {
        if (a && a.error) e.target.textContent = a.error;
      });
    });
    return {
      remove() {
        host.remove();
      },
      show(content, onRefill, refillLabel = "מלא שוב") {
        // The page takes over the whole document when it finishes starting and drops anything added
        // before, this panel included, so put it back whenever it has been removed.
        if (!host.isConnected) document.documentElement.appendChild(host);
        root.innerHTML = `
          <style>
            .box{position:fixed;left:12px;bottom:12px;z-index:2147483647;width:260px;max-height:40vh;overflow-y:auto;overflow-x:hidden;
              background:#fff;color:#18232b;border:1px solid #c9d3d8;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.15);
              font:14px/1.5 "Segoe UI",Arial,sans-serif;direction:rtl;padding:12px 14px}
            h1{font-size:15px;margin:0 0 6px} ul{margin:4px 0 8px;padding:0;list-style:none} li{direction:ltr;text-align:right;unicode-bidi:isolate} li::after{content:" •";color:#b0621a} .folder{font:inherit;font-size:13px;border:0;background:none;color:#1d6b5a;text-decoration:underline;cursor:pointer;padding:0;margin:2px 0}
            .muted{color:#5a6a74} .warn{color:#b0621a} .draft{color:#2f6fd6} .doubt{color:#c0392b;font-size:12px;direction:ltr} .files{margin:4px 0} a{color:#1d6b5a} .row{display:flex;gap:8px;margin-top:8px}
            button{font:inherit;border:1px solid #c9d3d8;background:#f3f6f7;border-radius:6px;padding:4px 10px;cursor:pointer}
          </style>
          <div class="box" role="status"></div>`;
        const box = root.querySelector(".box");
        box.innerHTML = content;
        const row = document.createElement("div");
        row.className = "row";
        if (onRefill) {
          const again = document.createElement("button");
          again.textContent = refillLabel;
          again.onclick = onRefill;
          row.appendChild(again);
        }
        const close = document.createElement("button");
        close.textContent = "הסתר";
        close.onclick = () => host.remove();
        row.appendChild(close);
        box.appendChild(row);
      },
    };
  }

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }

  // The exact files handed to the form, kept so the panel can open the very copy that was attached.
  const attached = {};
  const links = {};
  const FILE_NAMES = { resume: "קורות החיים", cover_letter: "המכתב", transcript: "גיליון הציונים" };

  function fileLinks() {
    return Object.entries(attached).map(([kind, file]) => {
      links[kind] = links[kind] || URL.createObjectURL(file);
      return `<a href="${links[kind]}" target="_blank" rel="noopener">פתח את ${FILE_NAMES[kind] || kind} שצורפו</a>`;
    }).join("<br>");
  }

  function summary(plan, outcome, drafting) {
    const list = (items) => `<ul>${items.map((e) => `<li>${escape(e.label)}${
      e.flag ? `<div class="doubt">הבדיקה מפקפקת: ${escape(e.flag)}</div>` : ""}</li>`).join("")}</ul>`;
    return `<h1>job-agent</h1>
      <div class="muted">${escape(plan.company)}</div>
      <div>מולאו ${outcome.filled.length} שדות${outcome.files.length ? `, צורפו ${outcome.files.length} קבצים` : ""}.</div>
      ${Object.keys(attached).length ? `<div class="files">${fileLinks()}</div>` : ""}
      <div><button class="folder" data-folder>פתח את תיקיית הקבצים</button></div>
      ${drafting ? `<div class="muted">${escape(drafting)}</div>` : ""}
      ${outcome.drafted.length ? `<div class="draft">טיוטות של המודל, קרא לפני השליחה (${outcome.drafted.length}):</div>${list(outcome.drafted)}` : ""}
      ${outcome.waiting.length ? `<div class="warn">מחכים לך (${outcome.waiting.length}):</div>${list(outcome.waiting)}` : ""}
      ${outcome.failed.length ? `<div class="warn">לא הצלחתי למלא:</div>${list(outcome.failed)}` : ""}
      ${outcome.unsaved.length ? `<div class="doubt">מוצג אבל הטופס לא שמר, בחר שוב בעצמך:</div>${list(outcome.unsaved)}` : ""}
      <div class="muted">עבור על הטופס ושלח בכפתור של הטופס עצמו. ההגשה תירשם במעקב כשיופיע דף התודה.</div>`;
  }

  function snapshot(plan, number) {
    if (!active) return;
    const values = plan.fields
      .filter((e) => field(e))
      .map((e) => {
        const value = read(e);
        // A file field also says which file the extension put there, since the page may hide its name.
        return { id: e.id, label: e.label, kind: e.kind, source: e.source, value,
                 planned: e.kind === "file" && e.source === "file" ? e.value : undefined,
                 changed: e.kind !== "file" && !same(e, value) };
      });
    ask({ type: "pending", number, snapshot: { job_id: plan.job_id, values } });
    // Remembered as Submit is pressed, so a correction is kept even if the thank-you page is missed.
    ask({ type: "remember", job_id: plan.job_id, values });
  }

  async function report(number) {
    if (!active) return;
    const answer = await ask({ type: "submitted", number });
    if (answer && answer.application) {
      makePanel().show(`<h1>job-agent</h1><div>ההגשה נרשמה במעקב.</div>`);
    } else if (answer && answer.error) {
      makePanel().show(`<h1>job-agent</h1><div class="warn">ההגשה נשלחה, אבל הרישום נכשל: ${escape(answer.error)}</div>
        <div class="muted">אפשר לסמן אותה ידנית בדף המשרות.</div>`);
    }
  }

  function watchConfirmation(number) {
    let done = false;
    const check = () => {
      if (!done && confirmed()) {
        done = true;
        observer.disconnect();
        report(number);
      }
    };
    const observer = new MutationObserver(check);
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // A small offer to fill a form the report did not open, which waits for a click and fills nothing before it.
  function offer() {
    return new Promise((resolve) => {
      const panel = makePanel();
      panel.show(`<h1>job-agent</h1><div class="muted">הטופס לא נפתח מדף המשרות, ולכן לא מולא.</div>`,
                 () => (panel.remove(), resolve()), "מלא את הטופס");
    });
  }

  async function start() {
    const number = jobNumber();
    if (!number) return;
    if (confirmed()) return report(number);
    const form = await waitFor(() => document.querySelector(FORM), 15000);
    if (!form) return;
    // Only a form opened by the report's button fills on its own: it carries a mark in its address,
    // remembered for this tab so a reload fills again. Any other visit, such as reading the posting,
    // gets a button to fill on request and is otherwise left alone.
    const key = `job-agent-fill:${number}`;
    let wanted = location.hash.includes("job-agent-fill");
    try {
      if (wanted) sessionStorage.setItem(key, "1");
      else wanted = sessionStorage.getItem(key) === "1";
    } catch (_) {}
    if (!wanted) {
      await offer();
      try { sessionStorage.setItem(key, "1"); } catch (_) {}
    }
    const panel = makePanel();
    panel.show(`<h1>job-agent</h1><div class="muted">מחכה שהטופס יסיים להיטען...</div>`);
    const plan = await ask({ type: "plan", url: location.href });
    currentJob = plan.job_id;
    if (plan.error) return panel.show(`<h1>job-agent</h1><div class="warn">${escape(plan.error)}</div>`);
    await pageReady();
    // A second pass a moment later catches any field the page cleared while it was still settling.
    let note = "המודל כותב טיוטות לשאלות שנשארו, עד דקה...";
    const run = async () => {
      await fill(plan, true);
      await F.sleep(1500);
      await fill(plan, false, true);
      await settle(plan);
      if (active) panel.show(summary(plan, status(plan), note), run);
    };
    await run();
    // The questions no standing answer covers get the model's drafts, which arrive later; each one
    // fills only a field that is still empty, and is outlined in blue for the candidate to read.
    const drafted = await ask({ type: "drafts", job: plan.job_id });
    for (const answer of drafted.answers || []) {
      const entry = plan.fields.find((e) => e.id === answer.id);
      if (entry && isEmpty(entry.value)) Object.assign(entry, { value: answer.value, source: "model", flag: answer.flag || "" });
    }
    note = drafted.error ? `הטיוטות לא הגיעו: ${drafted.error}` : "";
    if (active) {
      await fill(plan, false);
      await settle(plan);
      panel.show(summary(plan, status(plan), note), run);
    }
    // Capture the values at the moment of sending, before the page replaces the form with its thank-you.
    // Listen on the document, because the page may have replaced the form element since it was found.
    document.addEventListener("submit", (e) => {
      if (e.target.matches && e.target.matches(FORM)) snapshot(plan, number);
    }, true);
    document.addEventListener("click", (e) => {
      if (e.target.closest('button[type="submit"], input[type="submit"]')) snapshot(plan, number);
    }, true);
    watchConfirmation(number);
  }

  // Start only while the toolbar button has the extension on. Turned off mid-way, it stops filling,
  // hides its panel and records nothing; turned on again, it starts on the page as it is.
  let active = false;
  let started = false;
  const launch = () => active && !started && ((started = true), start());
  chrome.storage.local.get("enabled").then(({ enabled = true }) => ((active = enabled), launch()));
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.enabled) return;
    active = changes.enabled.newValue !== false;
    if (!active) document.querySelectorAll("#job-agent-panel").forEach((n) => n.remove());
    launch();
  });
})();
