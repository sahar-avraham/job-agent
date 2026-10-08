// Runs in the page's own JavaScript world, because only there are the dropdown components' internals
// visible; content.js runs in a separate world and asks for a selection through window events.
// Greenhouse builds its dropdowns with react-select, whose components receive a selectOption
// function and the option list. Calling it picks an option the way a click would, and it works in a
// background tab too, where opening the menu by simulated events does not.
(function () {
  if (window.__jobAgentPage) return;
  window.__jobAgentPage = true;

  const normalize = (s) => String(s == null ? "" : s).toLowerCase().replace(/\s+/g, " ").trim();

  // Walk up from the input through React's tree to the component that holds the options.
  function selectApi(input) {
    const key = Object.keys(input).find((k) => k.startsWith("__reactFiber"));
    let fiber = key && input[key];
    for (let depth = 0; depth < 15 && fiber; depth++, fiber = fiber.return) {
      const props = fiber.memoizedProps || {};
      if (typeof props.selectOption === "function" && Array.isArray(props.options)) return props;
    }
    return null;
  }

  function find(options, wanted) {
    const target = normalize(wanted);
    const flat = options.flatMap((o) => (o && o.options ? o.options : [o]));
    return flat.find((o) => normalize(o.label) === target) || flat.find((o) => normalize(o.label).includes(target));
  }

  // The education dropdowns load their options from a search as the person types, so there is no
  // option list to pick from. Ask the same search the page uses, one candidate name after another,
  // and hand the first match to the dropdown in the shape the page's own autofill uses.
  async function pickEducation(request) {
    const input = document.getElementById(request.id);
    const api = input && selectApi(input);
    const base = window.ENV && window.ENV.JBEN_URL;
    const board = (location.pathname.match(/^\/([^/]+)\/jobs\//) || [])[1] || new URLSearchParams(location.search).get("for");
    if (!api || !base || !board) return false;
    for (const candidate of request.values) {
      const url = `${base}/v1/boards/${board}/education/${request.search}?term=${encodeURIComponent(candidate)}&page=1`;
      try {
        const items = (await (await fetch(url)).json()).items || [];
        const target = normalize(candidate);
        const item = items.find((i) => normalize(i.text) === target) || items.find((i) => normalize(i.text).includes(target));
        if (item) {
          api.selectOption({ label: item.text, value: String(item.id) });
          return true;
        }
      } catch (_) {
        // try the next name
      }
    }
    return false;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // The form's own record of its answers, the object it validates and sends on submit. Found by
  // walking up React's tree from the form to the state that holds keys like "question_123".
  function formAnswers() {
    const form = document.querySelector("#application-form, #application_form");
    const key = form && Object.keys(form).find((k) => k.startsWith("__reactFiber"));
    let fiber = key && form[key];
    for (let depth = 0; depth < 40 && fiber; depth++, fiber = fiber.return) {
      let hook = fiber.memoizedState;
      for (let n = 0; n < 80 && hook; n++, hook = hook.next) {
        const value = hook.memoizedState;
        if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).some((k) => /^question_\d+/.test(k))) {
          return value;
        }
      }
    }
    return null;
  }

  window.addEventListener("job-agent-state", (event) => {
    let request;
    try {
      request = JSON.parse(event.detail);
    } catch (_) {
      return;
    }
    const answers = formAnswers();
    const values = answers ? Object.fromEntries(request.ids.map((id) => [id, answers[id] ?? null])) : null;
    window.dispatchEvent(new CustomEvent("job-agent-selected", { detail: JSON.stringify({ token: request.token, ok: !!answers, values }) }));
  });

  // The city field searches places as the person types and fills the hidden coordinates when a
  // result is chosen. Give it the typed text through its own input handler, wait for its results,
  // then choose the matching one, so the page runs its own place lookup exactly as for a person.
  async function pickLocation(request) {
    const input = document.getElementById(request.id);
    let api = input && selectApi(input);
    if (!api || !api.selectProps || typeof api.selectProps.onInputChange !== "function") return false;
    for (const wanted of request.values) {
      api.selectProps.onInputChange(wanted, { action: "input-change", prevInputValue: "" });
      for (let attempt = 0; attempt < 20; attempt++) {
        await sleep(300);
        api = selectApi(input);
        const option = api && find(api.options, wanted);
        if (option) {
          api.selectOption(option);
          return true;
        }
      }
    }
    return false;
  }

  window.addEventListener("job-agent-location", async (event) => {
    let request;
    try {
      request = JSON.parse(event.detail);
    } catch (_) {
      return;
    }
    const ok = await pickLocation(request);
    window.dispatchEvent(new CustomEvent("job-agent-selected", { detail: JSON.stringify({ token: request.token, ok }) }));
  });

  window.addEventListener("job-agent-education", async (event) => {
    let request;
    try {
      request = JSON.parse(event.detail);
    } catch (_) {
      return;
    }
    const ok = await pickEducation(request);
    window.dispatchEvent(new CustomEvent("job-agent-selected", { detail: JSON.stringify({ token: request.token, ok }) }));
  });

  window.addEventListener("job-agent-select", (event) => {
    let request;
    try {
      request = JSON.parse(event.detail);
    } catch (_) {
      return;
    }
    const input = document.getElementById(request.id);
    const api = input && selectApi(input);
    let ok = !!api;
    for (const wanted of api ? request.values : []) {
      const option = find(api.options, wanted);
      if (option) api.selectOption(option);
      else ok = false;
    }
    window.dispatchEvent(new CustomEvent("job-agent-selected", { detail: JSON.stringify({ token: request.token, ok }) }));
  });
})();
