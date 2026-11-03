// The files of the job a tab was opened for, in a window of the extension's own, for a site whose pages
// the extension leaves as they are, which is LinkedIn.
(function () {
  const SERVER = "http://127.0.0.1:8777";
  const tabId = Number(new URLSearchParams(location.search).get("tab"));
  const ask = (message) => new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));
  const escape = (text) => String(text || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const body = document.getElementById("body");

  async function showJob(job) {
    const info = await ask({ type: "job", job });
    if (!info || info.error) {
      body.innerHTML = `<h1>job-agent</h1><div class="warn">${escape((info && info.error) || "השרת לא עונה. האם serve.py רץ?")}</div>`;
      return;
    }
    const links = [];
    if (info.resume) links.push(`<a target="_blank" href="${SERVER}/api/form/file?job=${encodeURIComponent(info.id)}&kind=resume">קורות החיים המותאמים (PDF)</a>`);
    if (info.cover_letter) links.push(`<a target="_blank" href="${SERVER}/api/form/file?job=${encodeURIComponent(info.id)}&kind=cover_letter">המכתב (PDF)</a>`);
    if (info.resume) links.push(`<button class="link" data-folder="${escape(info.id)}">פתח את תיקיית הקבצים</button>`);
    if (!info.resume && info.draft) links.push(`<a target="_blank" href="${SERVER}${info.draft}">הטיוטה, עוד לא אושרה</a>`);
    body.innerHTML = `
      <h1>job-agent</h1>
      <div class="muted">באתר הזה התוסף לא ממלא טפסים ולא משנה את הדף. הקבצים של המשרה כאן:</div>
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
      <div class="muted">לאיזו משרה אתה מגיש?</div>
      ${jobs.length ? jobs.map((j) => `<button class="link pick" data-job="${escape(j.id)}">${escape(j.company)} · ${escape(j.title)}${j.approved ? "" : " (טיוטה)"}</button>`).join("")
                    : '<div class="warn">אין משרות עם קבצים או טיוטות.</div>'}`;
  }

  document.addEventListener("click", async (e) => {
    const target = e.target;
    if (target.closest("[data-folder]")) {
      const answer = await ask({ type: "folder", job_id: target.closest("[data-folder]").getAttribute("data-folder") });
      if (answer && answer.error) target.textContent = answer.error;
    } else if (target.closest("[data-other]")) {
      showPicker();
    } else if (target.closest("[data-job]")) {
      const job = target.closest("[data-job]").getAttribute("data-job");
      await ask({ type: "bind-tab", job, tabId });
      showJob(job);
    }
  });

  ask({ type: "tab-job", tabId }).then(({ job } = {}) => (job ? showJob(job) : showPicker()));
})();
