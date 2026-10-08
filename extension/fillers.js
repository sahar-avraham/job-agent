// Put values into form controls the way a person typing would, so the page's own code accepts them.
// Adapted from avid-autofill (MIT, see THIRD_PARTY.md). Setting element.value directly does not work
// on React forms such as Greenhouse's: React keeps its own copy of the value and restores it on the
// next render, so these go through the browser's real input pipeline instead.
(function () {
  const JobAgent = (globalThis.JobAgent = globalThis.JobAgent || {});

  const nativeInputSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  const nativeTextareaSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
  const nativeSelectSetter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;

  function normalize(s) {
    return String(s == null ? "" : s).toLowerCase().replace(/\s+/g, " ").trim();
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  // Type into a text input or textarea. execCommand fires the genuine input events React listens to;
  // the native setter is the fallback for a field that refuses it.
  function setTextValue(el, value) {
    el.focus();
    try {
      el.setSelectionRange ? el.setSelectionRange(0, (el.value || "").length) : el.select && el.select();
    } catch (_) {}
    let inserted = false;
    try {
      inserted = document.execCommand("insertText", false, value) && !!el.value;
    } catch (_) {}
    if (!inserted) {
      const setter = el.tagName === "TEXTAREA" ? nativeTextareaSetter : nativeInputSetter;
      setter.call(el, "");
      setter.call(el, value);
      el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: value }));
    }
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    el.blur();
  }

  // Type into a dropdown's filter box without blurring it, so the option list stays open.
  function setSearchValue(el, value) {
    el.focus();
    let inserted = false;
    try {
      inserted = document.execCommand("insertText", false, value) && !!el.value;
    } catch (_) {}
    if (!inserted) {
      nativeInputSetter.call(el, "");
      nativeInputSetter.call(el, value);
      el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: value }));
    }
  }

  // Pick an option of a plain <select> by its exact text, then by containment.
  function setNativeSelect(el, value) {
    const target = normalize(value);
    const options = Array.from(el.options);
    const match =
      options.find((o) => normalize(o.textContent) === target || normalize(o.value) === target) ||
      options.find((o) => normalize(o.textContent).includes(target));
    if (!match) return false;
    nativeSelectSetter.call(el, match.value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  // Pick an option of a react-select dropdown: open it, type to filter, then click the option.
  // Options render in a portal outside the control, so the whole document is searched.
  async function setReactSelect(input, value) {
    const target = normalize(value);
    const control = input.closest('[class*="control"]') || input;
    control.scrollIntoView({ block: "center" });
    control.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    input.focus();
    setSearchValue(input, value);
    await sleep(250);
    const text = (o) => normalize(o.textContent);
    let pick = null;
    for (let attempt = 0; attempt < 10 && !pick; attempt++) {
      const shown = Array.from(document.querySelectorAll('[role="option"], [class*="option"]')).filter(
        (o) => o.getClientRects().length > 0
      );
      pick = shown.find((o) => text(o) === target) || shown.find((o) => text(o).includes(target));
      if (!pick) await sleep(200);
    }
    if (!pick) {
      input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" }));
      return false;
    }
    pick.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    pick.click();
    await sleep(100);
    return true;
  }

  // Hand a file to a file input. Its FileList is read-only, and DataTransfer is the sanctioned
  // way to build one; the change event then starts the page's own upload.
  function uploadToInput(input, file) {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return true;
  }

  JobAgent.fillers = { normalize, sleep, setTextValue, setNativeSelect, setReactSelect, uploadToInput };
})();
