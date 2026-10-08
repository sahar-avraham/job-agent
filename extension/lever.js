// Runs on Lever application pages. Lever's form is one plain HTML page: the standard fields, each
// company's own questions ("cards"), and the equal-opportunity questions. Each field is read with its
// label and answered by the local server, as on Workday; the CV is attached first, since Lever reads
// it into the name, email and phone fields. It never presses Submit.
(function () {
  if (!/(^|\.)lever\.co$/.test(location.hostname)) return;
  const F = JobAgent.fillers;
  const FORM = "#application-form";

  function ask(message) {
    return new Promise((resolve) =>
      chrome.runtime.sendMessage(message, (answer) => resolve(answer || { error: "התוסף לא ענה" }))
    );
  }

  // The posting's own id, which ends the job's link and stays in the address through /apply and /thanks.
  function jobKey() {
    const match = location.pathname.match(/\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
    return match && match[1];
  }

  const clean = (text) => String(text || "").replace(/✱|\*/g, "").replace(/\s+/g, " ").trim();
  const matches = (text, wanted) => {
    const a = F.normalize(text), b = F.normalize(wanted);
    return a === b || a.startsWith(b) || (b.length > 2 && a.includes(b));
  };

  function labelOf(el) {
    // The equal-opportunity lists are named by what they ask, such as eeo[gender].
    const named = (el.getAttribute("name") || "").match(/^eeo\[(\w+)\]/);
    if (named) return named[1];
    const question = el.closest("li.application-question, li, .application-question");
    // The label's own element first: the <label> around a field also holds the field's hints and messages.
    const own = question && (question.querySelector(".application-label") || question.querySelector(".text") ||
      question.querySelector("label"));
    if (!own) return clean(el.getAttribute("aria-label"));
    // A label may wrap its own control, whose options would otherwise be read as part of the label.
    const copy = own.cloneNode(true);
    copy.querySelectorAll("select, input, textarea, option").forEach((c) => c.remove());
    return clean(copy.textContent) || clean(el.getAttribute("aria-label"));
  }

  // Every field on the form, grouped so a set of radio buttons or checkboxes is one question.
  function readForm() {
    const form = document.querySelector(FORM);
    if (!form) return [];
    const fields = [];
    const groups = {};
    let n = 0;
    for (const el of form.querySelectorAll("input, textarea, select")) {
      const name = el.getAttribute("name") || "";
      if (el.type === "hidden" || el.type === "file" || el.type === "submit" || !name) continue;
      // Consent to be contacted is the candidate's own decision, so it is never ticked.
      if (/^consent\b/.test(name)) continue;
      if (el.type === "radio" || el.type === "checkbox") {
        (groups[name] = groups[name] || []).push(el);
        continue;
      }
      const kind = el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : "text";
      const options = el.tagName === "SELECT" ? Array.from(el.options).map((o) => clean(o.textContent)).filter((t) => t && !/^select/i.test(t)) : [];
      fields.push({ key: `f${n++}`, el, name, label: labelOf(el), kind, options });
    }
    for (const [name, boxes] of Object.entries(groups)) {
      const kind = boxes[0].type === "radio" ? "radio" : "multiselect";
      const options = boxes.map((b) => clean((b.closest("label") || {}).innerText || b.value));
      fields.push({ key: `f${n++}`, el: boxes[0], boxes, name, label: labelOf(boxes[0]), kind, options });
    }
    return fields.filter((f) => f.label);
  }

  function current(field) {
    if (field.boxes) {
      return field.boxes.filter((b) => b.checked).map((b) => clean((b.closest("label") || {}).innerText || b.value)).join(", ");
    }
    if (field.kind === "select") {
      const option = field.el.options[field.el.selectedIndex];
      return option && option.value ? clean(option.textContent) : "";
    }
    return field.el.value || "";
  }

  function fillField(field, candidates) {
    if (field.boxes) {
      let any = false;
      const wanted = field.kind === "radio" ? candidates.slice(0, 1) : candidates;
      for (const w of wanted) {
        const i = field.options.findIndex((o) => matches(o, w));
        if (i >= 0 && !field.boxes[i].checked) {
          field.boxes[i].click();
          any = true;
        }
      }
      return any;
    }
    if (field.kind === "select") return candidates.some((c) => F.setNativeSelect(field.el, c));
    F.setTextValue(field.el, String(candidates[0]));
    return true;
  }

  // The CV goes in first and Lever reads it, so the page is given a moment to settle afterwards.
  async function attachResume(files) {
    const input = document.querySelector('input[type="file"][name="resume"]');
    const info = files && files.resume;
    if (!input || !info || !info.ok || (input.files && input.files.length)) return null;
    const answer = await ask({ type: "file", path: info.path });
    if (answer.error) return null;
    const bytes = Uint8Array.from(atob(answer.base64), (c) => c.charCodeAt(0));
    const file = new File([bytes], info.name, { type: "application/pdf" });
    F.uploadToInput(input, file);
    await quiet(1500, 15000);
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
      watch.observe(document.body, { childList: true, subtree: true, attributes: true });
      function done() {
        watch.disconnect();
        clearTimeout(timer);
        clearTimeout(cap);
        resolve();
      }
    });
  }

  // A small panel in its own shadow root, the same as on the other boards.
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

  async function fill() {
    show(`<h1>job-agent</h1><div class="muted">ממלא את הטופס...</div>`);
    const first = await ask({ type: "answers", url: location.href, questions: [] });
    if (first.error) return show(`<h1>job-agent</h1><div class="warn">${escape(first.error)}</div>`);
    plan = first;
    attached = attached || (await attachResume(plan.files));
    // Read after the CV, since Lever may have filled some fields from it by then.
    fields = readForm();
    const reply = await ask({ type: "answers", url: location.href,
      questions: fields.map(({ key, label, kind, options }) => ({ key, label, kind: kind === "radio" ? "choice" : kind, options })) });
    if (reply.error) return show(`<h1>job-agent</h1><div class="warn">${escape(reply.error)}</div>`);
    const byKey = Object.fromEntries((reply.answers || []).map((a) => [a.key, a]));
    const filled = [], waiting = [];
    for (const field of fields) {
      const answer = byKey[field.key] || {};
      field.source = answer.source || "none";
      field.value = answer.value;
      let candidates = Array.isArray(answer.value) ? answer.value : answer.value ? [String(answer.value)] : [];
      // Lever has no place for a letter file; its "Additional information" box takes the approved letter.
      if (!candidates.length && field.name === "comments" && plan.letter) {
        candidates = [plan.letter];
        field.source = "letter";
      }
      const now = current(field);
      // A value Lever read from the CV is replaced only by the candidate's own earlier answer or a
      // personal detail from answers.md; any other answer fills only an empty field.
      const ours = answer.source === "memory" || answer.source === "identity";
      if (now && !(ours && candidates.length && !candidates.some((c) => matches(now, c)))) continue;
      let ok = false;
      try {
        ok = candidates.length ? fillField(field, candidates) : false;
      } catch (_) {
        ok = false;
      }
      (ok && current(field) ? filled : waiting).push(field);
    }
    const list = (items) => `<ul>${items.map((f) => `<li>${escape(f.label)}</li>`).join("")}</ul>`;
    // The folder button shows whether or not a CV was attached here, since the files may be wanted anyway.
    const cv = (attached ? `<div><a href="${URL.createObjectURL(attached)}" target="_blank" rel="noopener">פתח את קורות החיים שצורפו</a></div>` : "") + `<div><button class="folder" data-folder>פתח את תיקיית הקבצים</button></div>`;
    show(`<h1>job-agent</h1><div class="muted">${escape(plan.company || "")}</div>
      <div>מולאו ${filled.length} שדות.</div>${cv}
      ${waiting.length ? `<div class="warn">מחכים לך (${waiting.length}):</div>${list(waiting)}` : ""}
      <div class="muted">עבור על הטופס ולחץ בעצמך על Submit application.</div>`,
      { label: "מלא שוב", onclick: fill });
  }

  // What the form held when Submit was pressed: kept for the record, and remembered for the next form.
  // Pressing Submit also leaves a note in this tab, since the thanks page that proves it was sent
  // comes next, and the record waits for it.
  const SUBMIT_NOTE = "job-agent-submit";
  function snapshot() {
    const key = jobKey();
    if (!active || !key || !plan.job_id) return;
    const values = readForm().map((f) => {
      const was = fields.find((g) => g.name === f.name) || {};
      const value = current(f);
      const want = Array.isArray(was.value) ? was.value : was.value ? [String(was.value)] : [];
      return { id: f.name, label: f.label, kind: f.kind === "radio" ? "select" : f.kind, source: was.source || "none",
               value, changed: !!value && !want.some((w) => matches(value, w)) };
    });
    ask({ type: "pending", number: key, snapshot: { job_id: plan.job_id, values } });
    ask({ type: "remember", job_id: plan.job_id, values });
    try { sessionStorage.setItem(SUBMIT_NOTE, JSON.stringify({ key, at: Date.now() })); } catch (_) {}
  }

  document.addEventListener("click", (e) => {
    const button = e.target.closest('button, input[type="submit"]');
    if (button && (button.id === "btn-submit" || /submit application/i.test(button.innerText || button.value || ""))) snapshot();
  }, true);

  function start() {
    const key = jobKey();
    if (!key) return;
    // The thanks page after Submit records the application, matched to the note left by Submit.
    if (/\/thanks\b/.test(location.pathname)) {
      let note = null;
      try { note = JSON.parse(sessionStorage.getItem(SUBMIT_NOTE) || "null"); } catch (_) {}
      if (note && note.key === key && Date.now() - note.at < 20 * 60000) {
        try { sessionStorage.removeItem(SUBMIT_NOTE); } catch (_) {}
        ask({ type: "submitted", number: key }).then((a) =>
          a && a.application && show(`<h1>job-agent</h1><div>ההגשה נרשמה במעקב.</div>`));
      }
      return;
    }
    if (!document.querySelector(FORM)) return;
    // Only a form opened by the report's button fills on its own; the mark is remembered for this tab.
    const flag = `job-agent-fill:${key}`;
    let wanted = location.hash.includes("job-agent-fill");
    try {
      if (wanted) sessionStorage.setItem(flag, "1");
      else wanted = sessionStorage.getItem(flag) === "1";
    } catch (_) {}
    if (wanted) return fill();
    show(`<h1>job-agent</h1><div class="muted">הטופס לא נפתח מדף המשרות, ולכן לא מולא.</div>`, {
      label: "מלא את הטופס",
      onclick: () => {
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
