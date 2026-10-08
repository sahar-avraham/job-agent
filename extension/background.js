// The only part of the extension that talks to the local server. Content scripts run inside the
// Greenhouse page and are bound by its rules, so they ask here instead; the host permission in the
// manifest lets this worker reach 127.0.0.1 and nothing else.
const SERVER = "http://127.0.0.1:8777";

// The toolbar button turns the extension on and off. Off, it fills nothing, shows nothing and
// records nothing, so a form can be filled by hand without it touching the page.
async function showState() {
  const { enabled = true } = await chrome.storage.local.get("enabled");
  // Both states are labelled, so a click always shows a visible change.
  await chrome.action.setBadgeText({ text: enabled ? "ON" : "OFF" });
  await chrome.action.setBadgeBackgroundColor({ color: enabled ? "#1d6b5a" : "#8a8f94" });
  await chrome.action.setTitle({ title: enabled ? "job-agent: on, click to turn off" : "job-agent: off, click to turn on" });
}

chrome.action.onClicked.addListener(async () => {
  const { enabled = true } = await chrome.storage.local.get("enabled");
  await chrome.storage.local.set({ enabled: !enabled });
  await showState();
});

chrome.runtime.onStartup.addListener(showState);
chrome.runtime.onInstalled.addListener(showState);

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  handle(message)
    .then(reply)
    .catch(() => reply({ error: "השרת של job-agent לא עונה. האם serve.py רץ?" }));
  return true; // the reply comes later
});

async function handle(message) {
  if (message.type === "plan") {
    const response = await fetch(`${SERVER}/api/form/plan?url=${encodeURIComponent(message.url)}`);
    return response.json();
  }
  // A form with no published question list sends its fields as the page shows them.
  if (message.type === "answers") {
    const response = await fetch(`${SERVER}/api/form/answers`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: message.url, questions: message.questions }),
    });
    return response.json();
  }
  // Written by the model on the first request for a job, which takes up to a minute; kept after that.
  if (message.type === "drafts") {
    const response = await fetch(`${SERVER}/api/form/drafts?job=${encodeURIComponent(message.job)}`);
    return response.json();
  }
  if (message.type === "file") {
    const response = await fetch(SERVER + message.path);
    if (!response.ok) return { error: "הקובץ לא נמצא בשרת" };
    return { base64: toBase64(await response.arrayBuffer()) };
  }
  // What the form held when submit was pressed, kept until the confirmation page proves it was sent.
  if (message.type === "pending") {
    await chrome.storage.session.set({ [`pending:${message.number}`]: message.snapshot });
    return { ok: true };
  }
  // A step's answers as it is saved, so a correction is remembered for the next form at once.
  if (message.type === "remember") {
    const response = await fetch(`${SERVER}/api/form/remember`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ job_id: message.job_id, values: message.values }),
    });
    return response.json();
  }
  // Opens the job's approved files in the file explorer, for a form that takes them by hand.
  if (message.type === "folder") {
    const response = await fetch(`${SERVER}/api/form/folder`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ job_id: message.job_id }),
    });
    return response.json();
  }
  if (message.type === "submitted") {
    const key = `pending:${message.number}`;
    const snapshot = (await chrome.storage.session.get(key))[key];
    if (!snapshot) return { skipped: true };
    const response = await fetch(`${SERVER}/api/form/submitted`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(snapshot),
    });
    await chrome.storage.session.remove(key);
    return response.json();
  }
  return { error: "unknown message" };
}

// Messages carry only JSON, so a file travels as base64 and is rebuilt in the page.
function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}
