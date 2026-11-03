// Put on a page of a site the extension does not fill: it says so, and hands over the job's files.
// The background places it on a tab opened from the jobs page, or on a click of the toolbar button.
(function () {
  if (window.__jobAgentFilesPanel) return window.__jobAgentFilesPanel.refresh();

  const SERVER = "http://127.0.0.1:8777";
  const ask = (message) => new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));
  const escape = (text) => String(text || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const host = document.createElement("div");
  host.id = "job-agent-files";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>
      .box{position:fixed;left:12px;bottom:12px;z-index:2147483647;width:270px;max-height:50vh;overflow-y:auto;
        background:#fff;color:#18232b;border:1px solid #c9d3d8;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.15);
        font:14px/1.5 "Segoe UI",Arial,sans-serif;direction:rtl;padding:12px 14px}
      h1{font-size:15px;margin:0 0 4px} .muted{color:#5a6a74;font-size:13px} .warn{color:#b0621a;font-size:13px}
      .job{direction:ltr;text-align:right;unicode-bidi:isolate;font-weight:600;margin:4px 0}
      .links{display:flex;flex-direction:column;gap:4px;margin:8px 0}
      a,.link{color:#1d6b5a;font:inherit;font-size:13px;background:none;border:0;padding:0;text-align:right;cursor:pointer;text-decoration:underline}
      .pick{display:block;width:100%;text-align:right;direction:ltr;unicode-bidi:isolate;margin:3px 0}
      .close{position:absolute;top:6px;left:10px;border:0;background:none;font-size:16px;cursor:pointer;color:#5a6a74}
    </style>
    <div class="box"><button class="close" title="סגור">×</button><div class="body"></div></div>`;
  const body = root.querySelector(".body");
  root.querySelector(".close").addEventListener("click", () => host.remove());

  async function showJob(job) {
    const info = await ask({ type: "job", job });
    if (!info || info.error) {
      body.innerHTML = `<h1>job-agent</h1><div class="warn">${escape((info && info.error) || "השרת לא עונה. האם serve.py רץ?")}</div>`;
      return;
    }
    const links = [];
    if (info.resume) links.push(`<a target="_blank" href="${SERVER}/api/form/file?job=${encodeURIComponent(info.id)}&kind=resume">קורות החיים המותאמים (PDF)</a>`);
    if (info.cover_letter) links.push(`<a target="_blank" href="${SERVER}/api/form/file?job=${encodeURIComponent(info.id)}&kind=cover_letter">המכתב (PDF)</a>`);
    if (info.resume) links.push(`<button class="link" data-folder>פתח את תיקיית הקבצים</button>`);
    if (!info.resume && info.draft) links.push(`<a target="_blank" href="${SERVER}${info.draft}">הטיוטה, עוד לא אושרה</a>`);
    body.innerHTML = `
      <h1>job-agent</h1>
      <div class="muted">באתר הזה התוסף לא ממלא טפסים. הקבצים של המשרה כאן:</div>
      <div class="job">${escape(info.company)} · ${escape(info.title)}</div>
      ${info.resume ? "" : '<div class="warn">אין עדיין קבצים מאושרים. אשר והפק בדף המשרות.</div>'}
      <div class="links">${links.join("")}</div>
      <button class="link" data-other>משרה אחרת</button>`;
  }

  async function showPicker() {
    const list = await ask({ type: "ready" });
    const jobs = (list && list.jobs) || [];
    body.innerHTML = `
      <h1>job-agent</h1>
      <div class="muted">באתר הזה התוסף לא ממלא טפסים. לאיזו משרה אתה מגיש?</div>
      ${jobs.length ? jobs.map((j) => `<button class="link pick" data-job="${escape(j.id)}">${escape(j.company)} · ${escape(j.title)}${j.approved ? "" : " (טיוטה)"}</button>`).join("")
                    : '<div class="warn">אין משרות עם קבצים או טיוטות.</div>'}`;
  }

  root.addEventListener("click", async (e) => {
    const target = e.target;
    if (target.closest("[data-folder]")) {
      const { job } = await ask({ type: "tab-job" });
      const answer = await ask({ type: "folder", job_id: job });
      if (answer && answer.error) target.textContent = answer.error;
    } else if (target.closest("[data-other]")) {
      showPicker();
    } else if (target.closest("[data-job]")) {
      const job = target.closest("[data-job]").getAttribute("data-job");
      await ask({ type: "bind-tab", job });
      showJob(job);
    }
  });

  async function refresh() {
    if (!host.isConnected) document.documentElement.appendChild(host);
    const { job } = await ask({ type: "tab-job" });
    return job ? showJob(job) : showPicker();
  }

  window.__jobAgentFilesPanel = { refresh };
  refresh();
})();
