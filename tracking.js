// Sends the tracking page's forms to serve.py and reloads, since the page is rebuilt per request.
(function () {
  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json().then((data) => ({ ok: r.ok, data })));
  }

  // Disable the button while a request runs, so a double click cannot record the same thing twice.
  function submit(form, path, body) {
    const button = form.querySelector("button[type=submit]");
    const error = form.querySelector(".error");
    button.disabled = true;
    error.hidden = true;
    post(path, body)
      .then(({ ok, data }) => {
        if (ok) return location.reload();
        error.textContent = data.error || "השמירה נכשלה";
        error.hidden = false;
        button.disabled = false;
      })
      .catch(() => {
        error.textContent = "השרת לא עונה. האם serve.py עדיין רץ?";
        error.hidden = false;
        button.disabled = false;
      });
  }

  const values = (form) => Object.fromEntries(new FormData(form).entries());

  // Show only the applications in the chosen state, and remember the choice across the reloads
  // that follow every update.
  function showCategory(cat) {
    let shown = 0;
    document.querySelectorAll(".apps .app").forEach((card) => {
      const match = (card.dataset.cats || "").split(" ").includes(cat);
      card.hidden = !match;
      shown += match ? 1 : 0;
    });
    document.querySelectorAll(".filter").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cat === cat)));
    const empty = document.querySelector(".filter-empty");
    if (empty) empty.hidden = shown > 0 || !document.querySelector(".apps .app");
    try { localStorage.setItem("tracking-category", cat); } catch (e) {}
  }
  document.querySelectorAll(".filter").forEach((b) => b.addEventListener("click", () => showCategory(b.dataset.cat)));

  // Read the mailbox when the page opens, unless it was read in the last hour; the button forces it.
  // New replies reload the page so they appear in their place; otherwise only the time line changes.
  const mailState = document.getElementById("mail-state");
  const mailButton = document.querySelector(".mail-check");
  function checkMail(force) {
    if (!mailState) return;
    post("/api/mail-check", { force }).then(({ ok, data }) => {
      if (!ok || !data.running) return;
      mailState.textContent = "בודק מייל...";
      if (mailButton) mailButton.disabled = true;
      const timer = setInterval(() => {
        fetch("/api/mail-status").then((r) => r.json()).then((s) => {
          if (s.running) return;
          clearInterval(timer);
          const r = s.result || {};
          if ((r.recorded || 0) + (r.pending || 0) > 0) return location.reload();
          mailState.textContent = r.error ? `בדיקת המייל נכשלה: ${r.error}` : `המייל נבדק לאחרונה ${s.checked}. אין עדכונים חדשים.`;
          if (mailButton) mailButton.disabled = false;
        });
      }, 2000);
    });
  }
  if (mailButton) mailButton.addEventListener("click", () => checkMail(true));
  checkMail(false);
  let saved = "all";
  try { saved = localStorage.getItem("tracking-category") || "all"; } catch (e) {}
  if (document.querySelector(`.filter[data-cat="${saved}"]`)) showCategory(saved);

  document.addEventListener("click", (e) => {
    const card = e.target.closest(".app");
    if (!card) return;
    if (e.target.classList.contains("open-update")) {
      const form = card.querySelector("form.update");
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector("select").focus();
    }
    if (e.target.classList.contains("remove")) {
      const name = card.querySelector(".company").textContent;
      if (!confirm("להסיר את ההגשה ל-" + name + " יחד עם כל ההיסטוריה שלה?")) return;
      post("/api/remove", { application_id: card.dataset.app }).then(({ ok, data }) =>
        ok ? location.reload() : alert(data.error)
      );
    }
    if (e.target.classList.contains("mail-ignore")) {
      e.target.disabled = true;
      post("/api/mail-ignore", { message_id: card.dataset.message }).then(({ ok, data }) =>
        ok ? location.reload() : (alert(data.error), (e.target.disabled = false))
      );
    }
  });

  document.addEventListener("submit", (e) => {
    const form = e.target;
    e.preventDefault();
    if (form.classList.contains("update")) {
      submit(form, "/api/event", { application_id: form.closest(".app").dataset.app, ...values(form) });
    } else if (form.classList.contains("mail-confirm")) {
      submit(form, "/api/mail-confirm", { message_id: form.closest(".app").dataset.message, ...values(form) });
    } else if (form.classList.contains("manual")) {
      submit(form, "/api/application", values(form));
    }
  });
})();
