// Runs on SmartRecruiters application pages ("oneclick-ui"). The form is built of web components, each
// field inside its own shadow root, so fields are found by walking every shadow root and labelled from
// the label in the same root. Answers come from the local server by label, as on Workday and Lever.
// The CV goes to the drop zone under "Resume", the approved letter to the message for the hiring
// manager. It never presses Next or Submit, and never ticks a consent box.
(function () {
  if (!/smartrecruiters\.com$|smartr\.me$/.test(location.hostname)) return;
  const F = JobAgent.fillers;
  const DONE = /application (has been |was )?(sent|submitted|received)|thank you for (applying|your application)/i;

  function ask(message) {
    return new Promise((resolve) =>
      chrome.runtime.sendMessage(message, (answer) => resolve(answer || { error: "התוסף לא ענה" }))
    );
  }

  // The posting's publication id, which names it in the form's address.
  function jobKey() {
    const match = location.pathname.match(/\/publication\/([0-9a-f-]{36})/i);
    return match && match[1];
  }

  const clean = (text) => String(text || "").replace(/\*/g, "").replace(/\s+/g, " ").trim();
  const matches = (text, wanted) => {
    const a = F.normalize(text), b = F.normalize(wanted);
    return a === b || a.startsWith(b) || (b.length > 2 && a.includes(b));
  };

  // Every element in the page and in every shadow root under it.
  function deep(root, test, out = []) {
    for (const el of root.querySelectorAll("*")) {
      if (test(el)) out.push(el);
      if (el.shadowRoot) deep(el.shadowRoot, test, out);
    }
    return out;
  }

  // The outermost component holding an element, whose surroundings name the section it sits in.
  function outerHost(el) {
    let root = el.getRootNode(), host = null;
    while (root && root.host) {
      host = root.host;
      root = host.getRootNode();
    }
    return host;
  }

  function sectionText(el) {
    let node = outerHost(el);
    for (let i = 0; i < 6 && node; i++) {
      node = node.parentElement;
      const text = node && clean(node.innerText);
      if (text && text.length > 3) return text;
    }
    return "";
  }

  function labelOf(el) {
    const root = el.getRootNode();
    const own = el.id && root.querySelector && root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return clean((own && own.innerText) || el.getAttribute("aria-label") || el.getAttribute("placeholder"));
  }

  const visible = (el) => el.getClientRects().length > 0;

  function readForm() {
    const fields = [];
    let n = 0;
    for (const el of deep(document, (e) => /^(INPUT|TEXTAREA)$/.test(e.tagName))) {
      if (!visible(el) || el.disabled || el.readOnly) continue;
      if (/^(hidden|file|submit|button|search|radio)$/.test(el.type)) continue;
      const label = labelOf(el);
      // A search box inside a list, such as the phone country search, is part of that list.
      if (!label || /^search/i.test(label)) continue;
      // Consent and terms are the candidate's own decision.
      if (el.type === "checkbox") continue;
      // An experience or education entry is filled from the facts, entry by entry, not as a question.
      if (inEntry(el)) continue;
      // A search list takes one of its own options, so it is asked as a choice and given every acceptable answer.
      const kind = el.tagName === "TEXTAREA" ? "textarea" : inAutocomplete(el) ? "choice" : "text";
      fields.push({ key: `f${n++}`, el, id: el.id, label, kind, options: [] });
    }
    return fields;
  }

  const current = (field) => field.el.value || "";

  // Experience and education are lists of entries. Each one is a small Angular form whose title field
  // takes a value only from real typing, so the entries are not written here: the tailored CV goes
  // to the drop zone that autocompletes the application, and SmartRecruiters builds them from it.
  const SECTIONS = { Experience: "OC-EXPERIENCE", Education: "OC-EDUCATION" };

  // Whether an element sits inside one of the lists, through the shadow roots between them.
  function inEntry(el) {
    for (let n = el; n; n = n.parentElement || (n.getRootNode() && n.getRootNode().host)) {
      if (Object.values(SECTIONS).includes(n.tagName)) return true;
    }
    return false;
  }

  // Whether the lists already hold an entry, from an earlier visit or an earlier upload.
  function hasEntries() {
    return Object.values(SECTIONS).some((tag) => {
      const box = deep(document, (e) => e.tagName === tag)[0];
      return box && clean(box.innerText).replace(/^(experience|education)\s*add\s*/i, "").length > 0;
    });
  }

  // After SmartRecruiters reads the CV into entries, they are put right: an entry it left open, such as
  // a role whose dates the CV does not print, gets them from the facts and is saved; an entry that is
  // none of the candidate's roles or schools, such as courses or languages read as a school, is deleted.
  // An entry it created can be completed this way; one the extension opens from nothing cannot,
  // because its title field takes a value only from real typing.
  const up = (n) => n.parentElement || (n.getRootNode() && n.getRootNode().host) || null;
  const pressable = (root, name) => deep(root, (e) => (e.tagName === "BUTTON" || e.tagName.startsWith("SPL-BUTTON")) &&
    visible(e) && new RegExp(`^${name}$`, "i").test(clean(e.innerText || e.textContent)));
  const field = (box, label) => deep(box, (e) => /^(INPUT|TEXTAREA)$/.test(e.tagName) && visible(e))
    .find((e) => new RegExp(`^${label}$`, "i").test(labelOf(e)));

  function typeDate(el, value) {
    if (!el || !value || el.value) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: value }));
    for (const t of ["keydown", "keyup"]) el.dispatchEvent(new KeyboardEvent(t, { key: "Enter", code: "Enter", keyCode: 13, bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    el.blur();
  }

  async function confirmYes() {
    await F.sleep(600);
    const dialog = deep(document, (e) => e.tagName === "SPL-DIALOG" && visible(e))[0];
    const yes = dialog && pressable(dialog, "yes")[0];
    if (yes) yes.click();
    await F.sleep(800);
  }

  // A field inside an autocomplete opens a list of suggestions as it is typed into. The suggestion that
  // starts with the typed value is picked, such as "Haifa, Israel" for Haifa; with none, the list is
  // closed, so it is never left open over the form.
  function inAutocomplete(el) {
    for (let n = el; n; n = up(n)) if (n.tagName === "SPL-AUTOCOMPLETE") return n;
    return null;
  }

  async function settleSuggestions(el, value) {
    const box = inAutocomplete(el);
    if (!box) return;
    let pick = null;
    for (let i = 0; i < 8 && !pick; i++) {
      await F.sleep(250);
      const options = deep(document, (e) => visible(e) && (e.getAttribute("role") === "option" || /^SPL-(DROPDOWN-ITEM|OPTION|LIST-ITEM)/.test(e.tagName)));
      const said = (o) => clean(o.innerText || o.textContent).toLowerCase();
      const typed = clean(value).toLowerCase();
      pick = options.find((o) => said(o).startsWith(typed)) || options.find((o) => said(o).includes(typed));
    }
    if (pick) pick.click();
    else {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", keyCode: 27, bubbles: true, composed: true }));
      el.blur();
    }
    await F.sleep(200);
    return !!pick;
  }

  const monthYear = (month, year) => (month && year ? `${month}/${year}` : "");
  const has = (text, part) => !!part && clean(text).toLowerCase().includes(clean(part).toLowerCase().replace(/^the /, ""));

  // Complete each entry SmartRecruiters left open and save it; an open entry that is none of the
  // candidate's roles or schools, such as the selected courses read as a second school, is cancelled.
  async function completeOpen(box, titleLabel, pick, fill) {
    for (const save of pressable(box, "save")) {
      let editor = save;
      while (editor && !field(editor, titleLabel)) editor = up(editor);
      if (!editor) continue;
      const entry = pick(editor);
      if (!entry) {
        const cancel = pressable(editor, "cancel")[0];
        if (cancel) cancel.click();
        await quiet(600, 3000);
        continue;
      }
      fill(editor, entry);
      await F.sleep(300);
      save.click();
      await quiet(800, 4000);
    }
  }

  // Delete each saved entry that names none of the candidate's roles or schools.
  async function deleteStrangers(box, kind, ours) {
    for (let guard = 0; guard < 10; guard++) {
      const stranger = deep(box, (e) => e.tagName === "BUTTON" && new RegExp(`^Delete ${kind}`, "i").test(e.getAttribute("aria-label") || ""))
        .find((b) => !ours.some((name) => has(b.getAttribute("aria-label"), name)));
      if (!stranger) return;
      stranger.click();
      await confirmYes();
    }
  }

  async function tidyEntries(experience) {
    if (!experience) return;
    const work = experience.work || [];
    const schools = experience.education || [];
    const exp = deep(document, (e) => e.tagName === "OC-EXPERIENCE")[0];
    if (exp) {
      await completeOpen(exp, "Title",
        (editor) => work.find((w) => has((field(editor, "Title") || {}).value, w.title) || has((field(editor, "Company") || {}).value, w.company)),
        (editor, w) => {
          typeDate(field(editor, "From"), monthYear(w.from_month, w.from_year));
          typeDate(field(editor, "To"), monthYear(w.to_month, w.to_year));
        });
      await deleteStrangers(exp, "experience", work.map((w) => w.company));
    }
    const edu = deep(document, (e) => e.tagName === "OC-EDUCATION")[0];
    if (edu) {
      await completeOpen(edu, "Institution",
        (editor) => schools.find((e) => has((field(editor, "Institution") || {}).value, e.school)),
        (editor, e) => {
          typeDate(field(editor, "From"), monthYear(e.from_month, e.from_year));
          typeDate(field(editor, "To"), monthYear(e.to_month, e.to_year));
        });
      await deleteStrangers(edu, "education", schools.map((e) => e.school));
    }
  }

  // The drop zones: one under "Resume", and one at the top that reads a CV into the whole application.
  function dropZone(which) {
    return deep(document, (e) => e.tagName === "INPUT" && e.type === "file").find((e) => {
      const around = sectionText(e);
      return which === "autofill" ? /apply with|autocomplete/i.test(around) : /resume|cv/i.test(around) && !/apply with/i.test(around);
    });
  }

  async function upload(zone, files) {
    const info = files && files.resume;
    if (!zone || !info || !info.ok) return null;
    const answer = await ask({ type: "file", path: info.path });
    if (answer.error) return null;
    const bytes = Uint8Array.from(atob(answer.base64), (c) => c.charCodeAt(0));
    const file = new File([bytes], info.name, { type: "application/pdf" });
    F.uploadToInput(zone, file);
    await quiet(2000, 25000);
    return file;
  }

  async function attachResume(files) {
    let file = null;
    // The top zone first, so SmartRecruiters writes the experience and education from the tailored CV.
    if (!hasEntries()) file = await upload(dropZone("autofill"), files);
    const zone = dropZone("resume");
    // A file already listed under Resume, from the top zone or an earlier visit, is not sent again.
    if (zone && !/\.pdf|\.docx?/i.test(sectionText(zone).replace(/choose a file.*$/i, ""))) file = (await upload(zone, files)) || file;
    return file;
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
  let fields = [];
  let attached = null;
  let active = false;
  let wanted = false;
  let filling = false;
  const seen = new Set();

  async function fill() {
    if (!active || !wanted || filling) return;
    filling = true;
    try {
      show(`<h1>job-agent</h1><div class="muted">ממלא את הטופס...</div>`);
      const first = await ask({ type: "answers", url: location.href, questions: [] });
      if (first.error) return show(`<h1>job-agent</h1><div class="warn">${escape(first.error)}</div>`);
      plan = first;
      attached = attached || (await attachResume(plan.files));
      await tidyEntries(plan.experience);
      fields = readForm();
      fields.forEach((f) => seen.add(f.id || f.label));
      const reply = await ask({ type: "answers", url: location.href,
        questions: fields.map(({ key, label, kind, options }) => ({ key, label, kind, options })) });
      if (reply.error) return show(`<h1>job-agent</h1><div class="warn">${escape(reply.error)}</div>`);
      const byKey = Object.fromEntries((reply.answers || []).map((a) => [a.key, a]));
      const filled = [], waiting = [];
      for (const field of fields) {
        const answer = byKey[field.key] || {};
        field.source = answer.source || "none";
        field.value = answer.value;
        let candidates = Array.isArray(answer.value) ? answer.value : answer.value ? [String(answer.value)] : [];
        // The message to the hiring manager takes the approved letter.
        if (!candidates.length && /hiring.?manager|let the company know|message/i.test(`${field.id} ${field.label}`) && plan.letter) {
          candidates = [plan.letter];
          field.source = "letter";
        }
        const now = current(field);
        const ours = answer.source === "memory" || answer.source === "identity";
        if (now && !(ours && candidates.length && !candidates.some((c) => matches(now, c)))) continue;
        if (candidates.length && field.kind === "choice" && inAutocomplete(field.el)) {
          // Each acceptable answer in turn, until the list offers one; with none, the box is left empty.
          let picked = false;
          for (const c of candidates) {
            F.setTextValue(field.el, String(c));
            if ((picked = await settleSuggestions(field.el, String(c)))) break;
          }
          if (!picked) F.setTextValue(field.el, "");
        } else if (candidates.length) {
          F.setTextValue(field.el, String(candidates[0]));
          await settleSuggestions(field.el, String(candidates[0]));
          field.el.dispatchEvent(new FocusEvent("focusout", { bubbles: true, composed: true }));
        }
        (candidates.length && current(field) ? filled : waiting).push(field);
      }
      const list = (items) => `<ul>${items.map((f) => `<li>${escape(f.label)}</li>`).join("")}</ul>`;
      // The folder button shows whether or not a CV was attached here, since the files may be wanted anyway.
    const cv = (attached ? `<div><a href="${URL.createObjectURL(attached)}" target="_blank" rel="noopener">פתח את קורות החיים שצורפו</a></div>` : "") + `<div><button class="folder" data-folder>פתח את תיקיית הקבצים</button></div>`;
      show(`<h1>job-agent</h1><div class="muted">${escape(plan.company || "")}</div>
        <div>מולאו ${filled.length} שדות.</div>${cv}
        ${waiting.length ? `<div class="warn">מחכים לך (${waiting.length}):</div>${list(waiting)}` : ""}
        <div class="muted">עבור על הטופס ולחץ בעצמך על הכפתור של SmartRecruiters כדי להמשיך.</div>`,
        { label: "מלא שוב", onclick: () => fill() });
    } finally {
      filling = false;
    }
  }

  // What the form held when its own button was pressed: kept for the record and remembered at once.
  // Submit also leaves a note in this tab, for the confirmation that follows.
  const SUBMIT_NOTE = "job-agent-submit";
  function snapshot(submitting) {
    const key = jobKey();
    if (!active || !wanted || !key || !plan.job_id) return;
    const values = readForm().map((f) => {
      const was = fields.find((g) => (g.id && g.id === f.id) || g.label === f.label) || {};
      const value = current(f);
      const want = Array.isArray(was.value) ? was.value : was.value ? [String(was.value)] : [];
      return { id: f.id, label: f.label, kind: f.kind, source: was.source || "none", value,
               changed: !!value && !want.some((w) => matches(value, w)) };
    });
    ask({ type: "pending", number: key, snapshot: { job_id: plan.job_id, values } });
    ask({ type: "remember", job_id: plan.job_id, values });
    if (submitting) try { sessionStorage.setItem(SUBMIT_NOTE, JSON.stringify({ key, at: Date.now() })); } catch (_) {}
  }

  document.addEventListener("click", (e) => {
    const button = e.composedPath().find((n) => n.tagName === "BUTTON" || (n.tagName && n.tagName.startsWith("SPL-BUTTON")));
    const text = clean(button && (button.innerText || button.textContent));
    if (/^(next|continue|submit|apply|send)/i.test(text)) snapshot(/^(submit|apply|send)/i.test(text));
  }, true);

  // A new page of the form, such as the screening questions after Next, fills when fields appear that
  // were not there before; the confirmation records the application, matched to the note left by Submit.
  let reported = false;
  const observer = new MutationObserver(() => {
    clearTimeout(observer.timer);
    observer.timer = setTimeout(async () => {
      const key = jobKey();
      let note = null;
      try { note = JSON.parse(sessionStorage.getItem(SUBMIT_NOTE) || "null"); } catch (_) {}
      if (!reported && note && note.key === key && Date.now() - note.at < 20 * 60000 && DONE.test(document.body.innerText)) {
        reported = true;
        try { sessionStorage.removeItem(SUBMIT_NOTE); } catch (_) {}
        const a = await ask({ type: "submitted", number: key });
        if (a && a.application) show(`<h1>job-agent</h1><div>ההגשה נרשמה במעקב.</div>`);
        return;
      }
      if (wanted && readForm().some((f) => !seen.has(f.id || f.label))) {
        await quiet(1200, 8000);
        fill();
      }
    }, 900);
  });

  function start() {
    const key = jobKey();
    if (!key) return;
    observer.observe(document.body, { childList: true, subtree: true });
    const flag = `job-agent-fill:${key}`;
    wanted = location.hash.includes("job-agent-fill");
    try {
      if (wanted) sessionStorage.setItem(flag, "1");
      else wanted = sessionStorage.getItem(flag) === "1";
    } catch (_) {}
    if (wanted) return quiet(1500, 12000).then(fill);
    show(`<h1>job-agent</h1><div class="muted">הטופס לא נפתח מדף המשרות, ולכן לא מולא.</div>`, {
      label: "מלא את הטופס",
      onclick: () => {
        wanted = true;
        try { sessionStorage.setItem(flag, "1"); } catch (_) {}
        fill();
      },
    });
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
