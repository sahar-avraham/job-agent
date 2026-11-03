// The only part of the extension that talks to the local server. Content scripts run inside other
// sites' pages and are bound by their rules, so they ask here instead.
const SERVER = "http://127.0.0.1:8777";

// Off, the extension fills nothing, shows nothing and records nothing, so a form can be filled by hand
// without it touching the page.
async function showState() {
  const { enabled = true } = await chrome.storage.local.get("enabled");
  // Both states are labelled, so a click always shows a visible change.
  await chrome.action.setBadgeText({ text: enabled ? "ON" : "OFF" });
  await chrome.action.setBadgeBackgroundColor({ color: enabled ? "#1d6b5a" : "#8a8f94" });
  await chrome.action.setTitle({ title: enabled ? "job-agent: on, click to turn off" : "job-agent: off, click to turn on" });
}

// Sites whose forms the extension fills. Anywhere else it can only show the job's files.
function fillable(url) {
  try {
    const { hostname, pathname } = new URL(url);
    return /(^|\.)greenhouse\.io$|\.myworkdayjobs\.com$|^jobs\.(eu\.)?lever\.co$/.test(hostname) ||
      (/(^|\.)smartrecruiters\.com$|(^|\.)smartr\.me$/.test(hostname) && pathname.startsWith("/oneclick-ui"));
  } catch (_) {
    return false;
  }
}

const jobsPage = (url) => (url || "").startsWith(SERVER);

// Open the files in a window of their own on LinkedIn, so its pages stay as they are.
function untouchable(url) {
  try {
    return /(^|\.)linkedin\.com$/.test(new URL(url).hostname);
  } catch (_) {
    return false;
  }
}

function filesWindow(tabId) {
  return chrome.windows.create({ url: `files.html?tab=${tabId}`, type: "popup", width: 340, height: 360 });
}

// On a site it fills, or on the jobs page, the button turns the extension on and off. Anywhere else it
// shows the files panel, for the job the tab was opened for or one picked from a list.
chrome.action.onClicked.addListener(async (tab) => {
  if (fillable(tab.url) || jobsPage(tab.url) || !/^https?:/.test(tab.url || "")) {
    const { enabled = true } = await chrome.storage.local.get("enabled");
    await chrome.storage.local.set({ enabled: !enabled });
    return showState();
  }
  return untouchable(tab.url) ? filesWindow(tab.id) : showFiles(tab.id);
});

async function showFiles(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["panel.js"] });
  } catch (_) {
    // A page the browser keeps scripts out of, such as the web store, gets no panel.
  }
}

// A job's link opened from the jobs page ties the tab it opens to that job, for a few seconds' window.
chrome.tabs.onCreated.addListener(async (tab) => {
  const { bindNext } = await chrome.storage.session.get("bindNext");
  if (bindNext && Date.now() - bindNext.at < 15000) {
    await chrome.storage.session.set({ [`tab:${tab.id}`]: bindNext.job });
    await chrome.storage.session.remove("bindNext");
  }
});

// Each page that tab reaches, on a site the extension does not fill, gets the files panel.
chrome.tabs.onUpdated.addListener(async (tabId, change, tab) => {
  if (change.status !== "complete" || !/^https?:/.test(tab.url || "") || fillable(tab.url) || jobsPage(tab.url)) return;
  const key = `tab:${tabId}`;
  const bound = (await chrome.storage.session.get(key))[key];
  const { enabled = true } = await chrome.storage.local.get("enabled");
  if (!bound || !enabled) return;
  if (untouchable(tab.url)) {
    // A mark on the button says the files are one click away, without touching the page.
    return chrome.action.setBadgeText({ tabId, text: "CV" });
  }
  showFiles(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(`tab:${tabId}`));

chrome.runtime.onStartup.addListener(showState);
chrome.runtime.onInstalled.addListener(showState);

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  handle(message, sender)
    .then(reply)
    .catch(() => reply({ error: "השרת של job-agent לא עונה. האם serve.py רץ?" }));
  return true; // the reply comes later
});

async function handle(message, sender) {
  if (message.type === "bind-next") {
    await chrome.storage.session.set({ bindNext: { job: message.job, at: Date.now() } });
    return { ok: true };
  }
  if (message.type === "tab-job") {
    const key = `tab:${message.tabId || (sender.tab && sender.tab.id)}`;
    return { job: (await chrome.storage.session.get(key))[key] || null };
  }
  if (message.type === "bind-tab") {
    await chrome.storage.session.set({ [`tab:${message.tabId || sender.tab.id}`]: message.job });
    return { ok: true };
  }
  if (message.type === "job") {
    const response = await fetch(`${SERVER}/api/form/job?job=${encodeURIComponent(message.job)}`);
    return response.json();
  }
  if (message.type === "ready") {
    const response = await fetch(`${SERVER}/api/form/ready`);
    return response.json();
  }
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
