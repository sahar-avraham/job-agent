// Runs on the local jobs page. When a job's link is opened from it, the tab that opens is tied to that
// job, so on a site the extension does not fill it can still show the job's CV and letter.
(function () {
  document.addEventListener(
    "click",
    (e) => {
      const link = e.target.closest && e.target.closest("a[href^='http']");
      if (!link || link.host === location.host) return;
      // A job's card carries its id; a row in the near-miss table carries it on its remove button.
      const holder = link.closest("[data-id]") || (link.closest("tr") && link.closest("tr").querySelector("[data-id]"));
      const job = holder && holder.getAttribute("data-id");
      if (job) chrome.runtime.sendMessage({ type: "bind-next", job });
    },
    true
  );
})();
