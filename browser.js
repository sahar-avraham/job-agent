// Makes the report usable rather than only readable. Loaded only when the page is
// served by serve.py, because a file opened from disk has nothing to talk to.
(function () {
  const served = location.protocol.startsWith("http");
  const bar = document.getElementById("actions");
  const count = document.getElementById("count");
  const log = document.getElementById("log");
  const picks = () => [...document.querySelectorAll(".pick:checked")].map((b) => b.value);

  // Without a server the boxes would promise something that cannot happen.
  if (!served) {
    document.querySelectorAll(".pick, .served-only").forEach((b) => (b.style.display = "none"));
    return;
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json().then((data) => ({ ok: r.ok, data })));
  }

  document.addEventListener("click", (e) => {
    if (e.target.classList.contains("restore")) {
      e.target.disabled = true;
      post("/api/restore", { job_id: e.target.dataset.id }).then(({ ok, data }) =>
        ok ? location.reload() : (alert(data.error), (e.target.disabled = false))
      );
      return;
    }
    if (e.target.classList.contains("dismiss-row")) {
      e.target.disabled = true;
      post("/api/dismiss", { job_id: e.target.dataset.id }).then(({ ok, data }) =>
        ok ? e.target.closest("tr").remove() : (alert(data.error), (e.target.disabled = false))
      );
      return;
    }
    const card = e.target.closest(".job");
    if (!card) return;
    if (e.target.classList.contains("upload-folder")) {
      const button = e.target;
      button.disabled = true;
      post("/api/upload-folder", { job_id: card.dataset.id }).then(({ ok, data }) => {
        button.disabled = false;
        if (!ok) return alert(data.error || "לא הצלחתי להכין את התיקייה");
        button.textContent = "התיקייה נפתחה, גרור ממנה את הקבצים לטופס";
      });
      return;
    }
    if (e.target.classList.contains("dismiss")) {
      // No confirmation step, because a removed job can be brought back from the list at the bottom.
      e.target.disabled = true;
      post("/api/dismiss", { job_id: card.dataset.id }).then(({ ok, data }) => {
        if (!ok) return alert(data.error), (e.target.disabled = false);
        card.querySelector(".pick").checked = false;
        card.remove();
        refresh();
      });
      return;
    }
    if (e.target.classList.contains("submit-now")) {
      // Ask the server what would go out first, and send only after the dialog is confirmed.
      const button = e.target;
      button.disabled = true;
      post("/api/submit-preview", { job_id: card.dataset.id }).then(({ ok, data }) => {
        if (!ok) return alert(data.error), (button.disabled = false);
        const text =
          "להגיש עכשיו ל-" + data.company + "?\n\n" +
          "נשלח מהג'ימייל שלך אל: " + data.to + "\n" +
          "נושא: " + data.subject + "\n" +
          "קבצים: " + data.files.join(", ") + "\n\n" +
          "אי אפשר לבטל אחרי השליחה.";
        if (!confirm(text)) return (button.disabled = false);
        log.classList.add("on");
        log.textContent = "שולח...";
        log.scrollIntoView({ block: "center" });
        setBusy(true);
        post("/api/submit", { job_id: card.dataset.id }).then(({ ok, data }) => {
          if (!ok) return alert(data.error || "השרת עסוק בפעולה אחרת"), setBusy(false), (button.disabled = false);
          if (!polling) polling = setInterval(poll, 1000);
        });
      });
      return;
    }
    const form = card.querySelector(".sent-form");
    if (e.target.classList.contains("mark-sent")) form.hidden = !form.hidden;
    if (e.target.classList.contains("cancel")) form.hidden = true;
  });

  // Disable the button while saving, so a double click cannot record one application twice.
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!form.classList.contains("sent-form")) return;
    e.preventDefault();
    const save = form.querySelector("button[type=submit]");
    const error = form.querySelector(".error");
    save.disabled = true;
    error.hidden = true;
    fetch("/api/sent", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ job_id: form.dataset.id, ...Object.fromEntries(new FormData(form).entries()) }),
    })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        if (ok) return (location.href = "/tracking");
        error.textContent = data.error || "השמירה נכשלה";
        error.hidden = false;
        save.disabled = false;
      })
      .catch(() => {
        error.textContent = "השרת לא עונה. האם serve.py עדיין רץ?";
        error.hidden = false;
        save.disabled = false;
      });
  });

  function refresh() {
    const chosen = [...document.querySelectorAll(".pick:checked")];
    const n = chosen.length;
    bar.classList.toggle("on", n > 0);
    // The companies are named, so a job ticked long ago and forgotten shows before a button acts on it.
    const names = chosen.map((b) => {
      const card = b.closest(".job");
      const company = card && card.querySelector(".company");
      return company ? company.textContent.trim() : "";
    }).filter(Boolean);
    count.textContent = (n === 1 ? "משרה אחת נבחרה" : n + " משרות נבחרו") + (names.length ? ": " : "");
    const list = document.createElement("span");
    list.className = "names ltr";
    list.textContent = names.join(" · ");
    count.appendChild(list);
    // Kept in the browser, so a reload or a restarted server does not lose a selection.
    try { localStorage.setItem("picks", JSON.stringify(picks())); } catch (e) {}
  }
  const clearButton = document.getElementById("btn-clear");
  if (clearButton) clearButton.addEventListener("click", () => {
    document.querySelectorAll(".pick:checked").forEach((b) => (b.checked = false));
    refresh();
  });
  document.addEventListener("change", (e) => {
    if (e.target.classList.contains("pick")) refresh();
  });

  // Restore the last selection; a job that has since left the list is simply not found.
  try {
    const saved = JSON.parse(localStorage.getItem("picks") || "[]");
    document.querySelectorAll(".pick").forEach((b) => (b.checked = saved.includes(b.value)));
  } catch (e) {}

  // Show one board's jobs at a time, remembered across reloads since every button press reloads the page.
  // A hidden job is also unticked, so a batch action never reaches a job that is not on screen.
  const boardBar = document.getElementById("board-bar");
  if (boardBar) {
    const show = (board) => {
      if (!boardBar.querySelector(`button[data-board="${board}"]`)) board = "all";
      document.body.dataset.boardFilter = board;
      boardBar.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.board === board));
      if (board !== "all") {
        document.querySelectorAll(`.job:not([data-board="${board}"]) .pick`).forEach((b) => (b.checked = false));
      }
      refresh();
      try { localStorage.setItem("board-filter", board); } catch (e) {}
    };
    let saved = "all";
    try { saved = localStorage.getItem("board-filter") || "all"; } catch (e) {}
    show(saved);
    boardBar.addEventListener("click", (e) => {
      if (e.target.dataset.board) show(e.target.dataset.board);
    });
  }

  let polling = null;

  function setBusy(busy) {
    document.querySelectorAll(".bar-actions button").forEach((b) => (b.disabled = busy));
  }

  function poll() {
    fetch("/api/status")
      .then((r) => r.json())
      .then((s) => {
        log.textContent = s.lines.join("\n");
        log.scrollTop = log.scrollHeight;
        if (!s.running) {
          clearInterval(polling);
          polling = null;
          setBusy(false);
          // The page is regenerated per request, so a reload shows the new documents.
          if (s.reload) setTimeout(() => location.reload(), 1200);
        }
      });
  }

  function send(path) {
    const ids = picks();
    if (!ids.length) return;
    setBusy(true);
    log.classList.add("on");
    log.textContent = "starting...";
    fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ids }),
    }).then(() => {
      if (!polling) polling = setInterval(poll, 1000);
    });
  }

  // Check every selected job first, list what will go and what will not, and send only after one confirm.
  async function submitBatch() {
    const ids = picks();
    if (!ids.length) return;
    if (ids.length > 10) return alert("עד 10 משרות בכל הגשה מרוכזת. בטל את הסימון של חלק מהן.");
    setBusy(true);
    log.classList.add("on");
    log.textContent = "בודק את המשרות המסומנות...";
    const ready = [], refused = [];
    for (const id of ids) {
      const card = document.querySelector(`.job[data-id="${id}"]`);
      const name = card ? card.querySelector(".company").textContent + " · " + card.querySelector(".title").textContent : id;
      const { ok, data } = await post("/api/submit-preview", { job_id: id });
      (ok ? ready : refused).push({ id, name, why: ok ? data.to : data.error });
    }
    const text =
      (ready.length ? "יישלחו " + ready.length + " הגשות מהג'ימייל שלך:\n" + ready.map((r) => "• " + r.name).join("\n") : "אף משרה לא מוכנה להגשה.") +
      (refused.length ? "\n\nלא יישלחו:\n" + refused.map((r) => "• " + r.name + " — " + r.why).join("\n") : "") +
      (ready.length ? "\n\nההגשות יוצאות אחת אחרי השנייה, בהפרש של כ-20 שניות. אי אפשר לבטל אחרי השליחה." : "");
    if (!ready.length) { alert(text); setBusy(false); log.classList.remove("on"); return; }
    if (!confirm(text)) { setBusy(false); log.classList.remove("on"); return; }
    log.textContent = "שולח...";
    log.scrollIntoView({ block: "center" });
    const { ok, data } = await post("/api/submit-batch", { ids: ready.map((r) => r.id) });
    if (!ok) { alert(data.error || "השרת עסוק בפעולה אחרת"); setBusy(false); return; }
    if (!polling) polling = setInterval(poll, 1000);
  }

  // Open each selected form in Chrome, where the job-agent extension fills it; sending stays with the candidate.
  async function openForms() {
    const ids = picks();
    if (!ids.length) return;
    if (ids.length > 10) return alert("עד 10 טפסים בכל פעם. בטל את הסימון של חלק מהמשרות.");
    setBusy(true);
    log.classList.add("on");
    log.textContent = "בודק שהמשרות עדיין פתוחות ופותח את הטפסים...";
    const { ok, data } = await post("/api/open-forms", { ids });
    setBusy(false);
    if (!ok) return (log.textContent = data.error || "הפתיחה נכשלה");
    log.textContent =
      (data.opened.length ? "נפתחו בכרום:\n" + data.opened.map((n) => "• " + n).join("\n") : "לא נפתח אף טופס.") +
      (data.refused.length ? "\n\nלא נפתחו:\n" + data.refused.map((r) => "• " + r.name + " — " + r.why).join("\n") : "") +
      (data.opened.length ? "\n\nהתוסף ממלא כל טופס. עבור עליהם ושלח בכפתור של כל טופס." : "");
  }

  document.getElementById("btn-tailor").onclick = () => send("/api/tailor");
  // A page rendered by an older server has no such button, and the other buttons must still work then.
  const formsButton = document.getElementById("btn-forms");
  if (formsButton) formsButton.onclick = openForms;
  document.getElementById("btn-approve").onclick = () => send("/api/approve");
  document.getElementById("btn-submit-batch").onclick = submitBatch;

  // Go through the companies with several jobs one at a time: only that company's jobs show, with
  // what was already sent there, and the usual buttons on each card decide them. The place is kept
  // across the reloads that follow tailoring or approving.
  const groupsData = document.getElementById("company-groups");
  const focusBar = document.getElementById("focus-bar");
  if (groupsData && focusBar) {
    const groups = JSON.parse(groupsData.textContent);
    const shortlist = document.getElementById("shortlist");
    let index = -1;
    const cardsOf = (g) => g.ids.map((id) => document.querySelector(`.job[data-id="${id}"]`)).filter(Boolean);

    function show(i) {
      index = Math.max(0, Math.min(i, groups.length - 1));
      const g = groups[index];
      const mine = new Set(g.ids);
      document.body.classList.add("focus-mode");
      focusBar.hidden = false;
      // The board filter would hide a company's jobs from another system, so the focus starts from all of them.
      const everyBoard = document.querySelector('#board-bar button[data-board="all"]');
      if (everyBoard && document.body.dataset.boardFilter !== "all") everyBoard.click();
      // A job hidden while one company is in focus is also unticked, so no button acts on a job out of sight.
      shortlist.querySelectorAll(".job").forEach((c) => {
        c.hidden = !mine.has(c.dataset.id);
        const box = c.querySelector(".pick");
        if (c.hidden && box) box.checked = false;
      });
      refresh();
      focusBar.querySelector(".focus-company").textContent = g.company;
      focusBar.querySelector(".focus-step").textContent =
        `חברה ${index + 1} מתוך ${groups.length} · ${cardsOf(g).length} משרות כאן`;
      focusBar.querySelector(".focus-case").textContent = g.kind === "fresh"
        ? "עוד לא הגשת לאף משרה כאן. בחר את המתאימה ביותר והסר את השאר, או השאר כמה אם כולן מתאימות."
        : `כבר הגשת כאן: ${g.sent.map((s) => `${s.title} (${s.stage})`).join(", ")}. להגיש גם לאלה, או להסיר אותן?`;
      focusBar.querySelector(".focus-prev").disabled = index === 0;
      focusBar.querySelector(".focus-next").disabled = index === groups.length - 1;
      try { sessionStorage.setItem("dupes-focus", g.key); } catch (e) {}
      window.scrollTo({ top: 0 });
    }

    function exit() {
      index = -1;
      document.body.classList.remove("focus-mode");
      focusBar.hidden = true;
      shortlist.querySelectorAll(".job").forEach((c) => (c.hidden = false));
      try { sessionStorage.removeItem("dupes-focus"); } catch (e) {}
    }

    document.getElementById("btn-dupes").onclick = () => show(0);
    focusBar.querySelector(".focus-prev").onclick = () => show(index - 1);
    focusBar.querySelector(".focus-next").onclick = () => show(index + 1);
    focusBar.querySelector(".focus-exit").onclick = exit;

    // A removed job leaves the view; when a company has none left, move on to the next one.
    new MutationObserver(() => {
      if (index < 0) return;
      if (cardsOf(groups[index]).length) show(index);
      else if (index < groups.length - 1) show(index + 1);
      else exit();
    }).observe(shortlist, { childList: true });

    let saved = null;
    try { saved = sessionStorage.getItem("dupes-focus"); } catch (e) {}
    const at = groups.findIndex((g) => g.key === saved);
    if (at >= 0) show(at);
  }
  refresh();
})();
