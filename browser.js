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

  // Remember the filter across reloads, since every button press reloads the page.
  const onlyComeet = document.getElementById("only-comeet");
  if (onlyComeet) {
    const apply = () => document.body.classList.toggle("only-comeet", onlyComeet.checked);
    try { onlyComeet.checked = localStorage.getItem("only-comeet") === "1"; } catch (e) {}
    apply();
    onlyComeet.addEventListener("change", () => {
      try { localStorage.setItem("only-comeet", onlyComeet.checked ? "1" : "0"); } catch (e) {}
      apply();
    });
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
    const card = e.target.closest(".job");
    if (!card) return;
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
    const n = picks().length;
    bar.classList.toggle("on", n > 0);
    count.textContent = n === 1 ? "משרה אחת נבחרה" : n + " משרות נבחרו";
  }
  document.addEventListener("change", (e) => {
    if (e.target.classList.contains("pick")) refresh();
  });

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

  document.getElementById("btn-tailor").onclick = () => send("/api/tailor");
  document.getElementById("btn-approve").onclick = () => send("/api/approve");
  document.getElementById("btn-submit-batch").onclick = submitBatch;
  refresh();
})();
